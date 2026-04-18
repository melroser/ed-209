"""ED-209 — Uncertainty-aware OFAC sanctions screening backend."""

import logging
import time
from typing import Optional

import anthropic
import httpx
from fastapi import FastAPI, Query
from itertools import combinations
from jsonld_ex import Opinion, robust_fuse, pairwise_conflict, decay_opinion
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class EvidenceOpinion(BaseModel):
    b: float
    d: float
    u: float
    projected: float
    label: str
    note: str


class FusedOpinion(BaseModel):
    b: float
    d: float
    u: float
    projected: float


class DecisionResult(BaseModel):
    action: str
    color: str
    label: str


class SDNMatch(BaseModel):
    name: str
    type: str
    score: float
    program: str


class BinaryComparison(BaseModel):
    best_fuzzy_score: float
    threshold: float
    binary_decision: str
    our_decision: str
    difference: str


class ScreeningResponse(BaseModel):
    entity: str
    entity_type: str
    country: str
    sdn_hits: int
    sdn_results: list[SDNMatch]
    evidence: dict[str, EvidenceOpinion]
    fused: FusedOpinion
    decision: DecisionResult
    conflict_score: float
    outliers_removed: int
    binary_comparison: BinaryComparison
    ai_assessment: str
    latency_ms: int


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="ED-209", description="Uncertainty-aware OFAC sanctions screening")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# SDN Client
# ---------------------------------------------------------------------------

SDN_API_URL = "https://sdn-openapi.netlify.app/api/search"


async def query_sdn(name: str) -> tuple[list[dict], bool]:
    """Query the SDN OpenAPI for sanctions matches.

    Returns (results, sdn_available).
    - (results, True)  → successful lookup; results may be empty (no matches).
    - ([], False)       → API unreachable/error; caller MUST treat this as
      "we don't know" (vacuous opinions), NOT as "no matches found".
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(SDN_API_URL, params={"q": name})
            response.raise_for_status()
            data = response.json()
            return data.get("results", []), True
    except Exception as exc:
        logger.error("SDN API error for name=%r: %s", name, exc)
        return [], False


# ---------------------------------------------------------------------------
# Evidence Decomposer
# ---------------------------------------------------------------------------

# High-severity SDN programs
_SEVERE_PROGRAMS = {"SDGT", "WMD", "NPWMD", "IRAN", "DPRK", "SYRIA"}


def decompose_evidence(
    top_match: dict,
    all_matches: list[dict],
    screened_country: str,
    screened_type: str,
) -> dict[str, Opinion]:
    """Decompose an SDN match into 5 independent evidence Opinions.

    Returns a dict keyed by dimension name → Opinion.
    """
    opinions: dict[str, Opinion] = {}

    # 1. name_similarity — based on top match score
    score = top_match.get("score", 0)
    if score >= 0.95:
        opinions["name_similarity"] = Opinion(belief=0.80, disbelief=0.05, uncertainty=0.15)
    elif score >= 0.85:
        opinions["name_similarity"] = Opinion(belief=0.55, disbelief=0.15, uncertainty=0.30)
    elif score >= 0.70:
        opinions["name_similarity"] = Opinion(belief=0.30, disbelief=0.25, uncertainty=0.45)
    else:
        opinions["name_similarity"] = Opinion(belief=0.15, disbelief=0.35, uncertainty=0.50)

    # 2. entity_type — compare screened type vs SDN type
    sdn_type = top_match.get("type", "").lower().strip()
    if not sdn_type:
        opinions["entity_type"] = Opinion(belief=0.0, disbelief=0.0, uncertainty=1.0)
    elif screened_type.lower().strip() == sdn_type:
        opinions["entity_type"] = Opinion(belief=0.50, disbelief=0.10, uncertainty=0.40)
    else:
        opinions["entity_type"] = Opinion(belief=0.05, disbelief=0.70, uncertainty=0.25)

    # 3. geography — compare screened country vs SDN country/address fields
    country_lower = screened_country.lower().strip()
    sdn_country = top_match.get("country", "").lower().strip()
    sdn_address = top_match.get("address", "").lower().strip()
    sdn_addresses = top_match.get("addresses", "")
    # Flatten addresses if it's a list of dicts
    if isinstance(sdn_addresses, list):
        sdn_addresses = " ".join(
            str(a) if not isinstance(a, dict) else " ".join(str(v) for v in a.values())
            for a in sdn_addresses
        ).lower()
    else:
        sdn_addresses = str(sdn_addresses).lower()

    geo_text = f"{sdn_country} {sdn_address} {sdn_addresses}".strip()
    if not geo_text or geo_text.isspace():
        opinions["geography"] = Opinion(belief=0.0, disbelief=0.0, uncertainty=1.0)
    elif country_lower and country_lower in geo_text:
        opinions["geography"] = Opinion(belief=0.60, disbelief=0.05, uncertainty=0.35)
    else:
        opinions["geography"] = Opinion(belief=0.05, disbelief=0.65, uncertainty=0.30)

    # 4. program_severity — check SDN program
    program = top_match.get("program", "").strip()
    if not program:
        opinions["program_severity"] = Opinion(belief=0.0, disbelief=0.0, uncertainty=1.0)
    elif any(p in program.upper() for p in _SEVERE_PROGRAMS):
        opinions["program_severity"] = Opinion(belief=0.30, disbelief=0.10, uncertainty=0.60)
    else:
        opinions["program_severity"] = Opinion(belief=0.15, disbelief=0.15, uncertainty=0.70)

    # 5. alias_coverage — based on number of hits and second match score
    num_hits = len(all_matches)
    if num_hits == 0:
        opinions["alias_coverage"] = Opinion(belief=0.0, disbelief=0.70, uncertainty=0.30)
    else:
        second_score = all_matches[1].get("score", 0) if num_hits >= 2 else 0
        if num_hits >= 3 and second_score > 0.75:
            opinions["alias_coverage"] = Opinion(belief=0.55, disbelief=0.10, uncertainty=0.35)
        elif num_hits >= 2 and second_score > 0.60:
            opinions["alias_coverage"] = Opinion(belief=0.35, disbelief=0.20, uncertainty=0.45)
        else:
            opinions["alias_coverage"] = Opinion(belief=0.15, disbelief=0.25, uncertainty=0.60)

    return opinions


# ---------------------------------------------------------------------------
# Fusion Engine
# ---------------------------------------------------------------------------


def fuse_opinions(opinions: list[Opinion]) -> tuple[Opinion, list]:
    """Fuse a list of Opinions using robust_fuse with outlier removal.

    robust_fuse() returns a tuple: (fused_opinion, removed_indices)
    where removed_indices is a list of ints indicating which opinions
    were excluded as outliers.
    """
    fused, removed = robust_fuse(opinions)
    return fused, removed


def compute_conflict(opinions: list[Opinion]) -> float:
    """Compute the maximum pairwise conflict score across all opinion pairs.

    Uses pairwise_conflict(a, b) on every combination of 2 opinions.
    Returns 0.0 if fewer than 2 opinions are provided.
    """
    if len(opinions) < 2:
        return 0.0
    conflicts = [pairwise_conflict(a, b) for a, b in combinations(opinions, 2)]
    return max(conflicts)


# ---------------------------------------------------------------------------
# Decision Engine
# ---------------------------------------------------------------------------


def decide(opinion: Opinion) -> str:
    """Map a fused Opinion to one of four decision strings.

    Evaluated in order:
      AUTO_BLOCK  → b >= 0.6  AND u <= 0.25
      ESCALATE    → b >= 0.35 AND u > 0.25
      AUTO_CLEAR  → d >= 0.45 AND u <= 0.35
      GATHER_MORE → fallback
    """
    b, d, u = opinion.belief, opinion.disbelief, opinion.uncertainty
    if b >= 0.6 and u <= 0.25:
        return "AUTO_BLOCK"
    if b >= 0.35 and u > 0.25:
        return "ESCALATE"
    if d >= 0.45 and u <= 0.35:
        return "AUTO_CLEAR"
    return "GATHER_MORE"


# ---------------------------------------------------------------------------
# Binary Comparison
# ---------------------------------------------------------------------------

BINARY_THRESHOLD = 0.65


def binary_decision(top_score: float | None) -> dict:
    """What a traditional threshold-based system would decide.

    Threshold = 0.65; score >= threshold → FLAGGED, else → CLEARED.
    None score → score = 0.0, decision = CLEARED.
    """
    if top_score is None:
        return {"score": 0.0, "decision": "CLEARED", "threshold": BINARY_THRESHOLD}
    return {
        "score": top_score,
        "decision": "FLAGGED" if top_score >= BINARY_THRESHOLD else "CLEARED",
        "threshold": BINARY_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

HALF_LIFE_SECONDS = 14 * 86400  # 14 days = 1,209,600 seconds


def apply_decay(opinion: Opinion, days: int) -> Opinion:
    """Apply exponential decay with a 14-day half-life.

    Converts days → seconds and calls decay_opinion with keyword args.
    """
    return decay_opinion(
        opinion,
        elapsed_seconds=days * 86400,
        half_life_seconds=HALF_LIFE_SECONDS,
    )


# ---------------------------------------------------------------------------
# AI Analyst — Claude risk narratives
# ---------------------------------------------------------------------------


async def generate_risk_assessment(
    entity_name: str,
    sdn_matches: list[dict],
    fused_opinion: Opinion,
    decision: str,
) -> str:
    """Call Claude for a 3-sentence risk narrative.

    Returns a plain text string. On any API error or timeout,
    logs the error and returns a fallback string.
    """
    try:
        client = anthropic.AsyncAnthropic()

        match_summary = "No SDN matches found."
        if sdn_matches:
            top_names = [m.get("name", "Unknown") for m in sdn_matches[:3]]
            top_scores = [str(m.get("score", 0)) for m in sdn_matches[:3]]
            match_summary = (
                f"{len(sdn_matches)} SDN match(es). "
                f"Top: {', '.join(top_names)} (scores: {', '.join(top_scores)})"
            )

        prompt = (
            f"You are a compliance analyst. Write exactly 3 sentences summarizing "
            f"this OFAC sanctions screening result.\n\n"
            f"Entity: {entity_name}\n"
            f"SDN Matches: {match_summary}\n"
            f"Fused Opinion: belief={fused_opinion.belief:.3f}, "
            f"disbelief={fused_opinion.disbelief:.3f}, "
            f"uncertainty={fused_opinion.uncertainty:.3f}\n"
            f"Decision: {decision}\n\n"
            f"Be concise and factual. No headers or bullet points."
        )

        message = await client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )

        return message.content[0].text.strip()

    except Exception as exc:
        logger.error("AI risk assessment error: %s", exc)
        return "AI risk assessment unavailable."


# ---------------------------------------------------------------------------
# Decision metadata helpers
# ---------------------------------------------------------------------------

_DECISION_COLOR = {
    "AUTO_CLEAR": "green",
    "AUTO_BLOCK": "red",
    "ESCALATE": "amber",
    "GATHER_MORE": "blue",
}

_DECISION_LABEL = {
    "AUTO_CLEAR": "Auto Clear",
    "AUTO_BLOCK": "Auto Block",
    "ESCALATE": "Escalate",
    "GATHER_MORE": "Gather More",
}

_EVIDENCE_LABELS = {
    "name_similarity": "Name Similarity",
    "entity_type": "Entity Type",
    "geography": "Geography",
    "program_severity": "Program Severity",
    "alias_coverage": "Alias Coverage",
}

_DIMENSION_KEYS = ["name_similarity", "entity_type", "geography", "program_severity", "alias_coverage"]


def _projected(b: float, u: float) -> float:
    """Subjective Logic projected probability: P = b + a·u where a=0.5."""
    return round((b + 0.5 * u) * 100, 1)


def _build_evidence_dict(
    opinions: dict[str, Opinion],
    note_fn=None,
) -> dict[str, EvidenceOpinion]:
    """Convert a dict of dimension→Opinion into EvidenceOpinion models."""
    evidence: dict[str, EvidenceOpinion] = {}
    for key, op in opinions.items():
        note = note_fn(key) if note_fn else ""
        evidence[key] = EvidenceOpinion(
            b=op.belief,
            d=op.disbelief,
            u=op.uncertainty,
            projected=_projected(op.belief, op.uncertainty),
            label=_EVIDENCE_LABELS.get(key, key),
            note=note,
        )
    return evidence


def _build_screening_response(
    *,
    name: str,
    entity_type: str,
    country: str,
    results: list[dict],
    evidence_opinions: dict[str, Opinion],
    evidence_dict: dict[str, EvidenceOpinion],
    fused_op: Opinion,
    decision_str: str,
    conflict: float,
    outliers_removed: int,
    top_score: float | None,
    start_time: float,
    ai_assessment: str = "AI risk assessment unavailable.",
) -> ScreeningResponse:
    """Assemble the full ScreeningResponse."""
    binary = binary_decision(top_score)

    # Human-readable difference string
    if binary["decision"] == "FLAGGED" and decision_str == "AUTO_CLEAR":
        difference = "Traditional system flags this entity; ED-209 auto-clears based on multi-dimensional evidence"
    elif binary["decision"] == "CLEARED" and decision_str in ("AUTO_BLOCK", "ESCALATE"):
        difference = "Traditional system clears this entity; ED-209 detects risk via evidence fusion"
    elif binary["decision"] == "CLEARED" and decision_str == "AUTO_CLEAR":
        difference = "Both systems agree: no match found"
    elif binary["decision"] == "FLAGGED" and decision_str == "AUTO_BLOCK":
        difference = "Both systems agree: high risk entity"
    else:
        difference = f"Binary: {binary['decision']}; ED-209: {decision_str}"

    latency_ms = int((time.time() - start_time) * 1000)

    sdn_results = [
        SDNMatch(
            name=r.get("name", ""),
            type=r.get("type", ""),
            score=r.get("score", 0.0),
            program=r.get("program", ""),
        )
        for r in results
    ]

    return ScreeningResponse(
        entity=name,
        entity_type=entity_type,
        country=country,
        sdn_hits=len(results),
        sdn_results=sdn_results,
        evidence=evidence_dict,
        fused=FusedOpinion(
            b=fused_op.belief,
            d=fused_op.disbelief,
            u=fused_op.uncertainty,
            projected=_projected(fused_op.belief, fused_op.uncertainty),
        ),
        decision=DecisionResult(
            action=decision_str,
            color=_DECISION_COLOR[decision_str],
            label=_DECISION_LABEL[decision_str],
        ),
        conflict_score=round(conflict, 4),
        outliers_removed=outliers_removed,
        binary_comparison=BinaryComparison(
            best_fuzzy_score=binary["score"],
            threshold=binary["threshold"],
            binary_decision=binary["decision"],
            our_decision=decision_str,
            difference=difference,
        ),
        ai_assessment=ai_assessment,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# GET /api/screen — main screening orchestrator
# ---------------------------------------------------------------------------


@app.get("/api/screen")
async def screen(
    name: str = Query(..., min_length=1, max_length=256),
    country: str = Query(default="Unknown"),
    entity_type: str = Query(default="individual"),
) -> ScreeningResponse:
    """Run a full compliance screening for the given entity."""
    start_time = time.time()

    results, sdn_available = await query_sdn(name)
    results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)  # ADD THIS

    # --- SDN unavailable: vacuous opinions → GATHER_MORE ---
    if not sdn_available:
        vacuous = Opinion(belief=0.0, disbelief=0.0, uncertainty=1.0)
        opinions = {k: vacuous for k in _DIMENSION_KEYS}
        evidence_dict = _build_evidence_dict(
            opinions, note_fn=lambda _k: "SDN API unavailable"
        )
        fused_op = vacuous
        conflict = 0.0
        outliers_removed = 0
        decision_str = decide(fused_op)
        ai_text = await generate_risk_assessment(name, results, fused_op, decision_str)
        return _build_screening_response(
            name=name,
            entity_type=entity_type,
            country=country,
            results=results,
            evidence_opinions=opinions,
            evidence_dict=evidence_dict,
            fused_op=fused_op,
            decision_str=decision_str,
            conflict=conflict,
            outliers_removed=outliers_removed,
            top_score=None,
            start_time=start_time,
            ai_assessment=ai_text,
        )

    # --- Zero hits: no-match fast path → AUTO_CLEAR ---
    if len(results) == 0:
        no_match = Opinion(belief=0.02, disbelief=0.78, uncertainty=0.20)
        opinions = {k: no_match for k in _DIMENSION_KEYS}
        evidence_dict = _build_evidence_dict(
            opinions, note_fn=lambda _k: "No SDN matches found"
        )
        fused_op = no_match
        conflict = 0.0
        outliers_removed = 0
        decision_str = decide(fused_op)
        ai_text = await generate_risk_assessment(name, results, fused_op, decision_str)
        return _build_screening_response(
            name=name,
            entity_type=entity_type,
            country=country,
            results=results,
            evidence_opinions=opinions,
            evidence_dict=evidence_dict,
            fused_op=fused_op,
            decision_str=decision_str,
            conflict=conflict,
            outliers_removed=outliers_removed,
            top_score=0.0,
            start_time=start_time,
            ai_assessment=ai_text,
        )

    # --- Hits exist: decompose, fuse, decide ---
    top_match = results[0]
    top_score = top_match.get("score", 0.0)

    opinions = decompose_evidence(
        top_match=top_match,
        all_matches=results,
        screened_country=country,
        screened_type=entity_type,
    )

    opinion_list = list(opinions.values())
    fused_op, removed = fuse_opinions(opinion_list)
    conflict = compute_conflict(opinion_list)
    decision_str = decide(fused_op)

    def _note_fn(key: str) -> str:
        if key == "name_similarity":
            return f"SDN score: {top_score}"
        if key == "entity_type":
            return f"Screened: {entity_type}, SDN: {top_match.get('type', 'N/A')}"
        if key == "geography":
            return f"Screened: {country}, SDN: {top_match.get('country', 'N/A')}"
        if key == "program_severity":
            return f"Program: {top_match.get('program', 'N/A')}"
        if key == "alias_coverage":
            return f"{len(results)} SDN hit(s)"
        return ""

    evidence_dict = _build_evidence_dict(opinions, note_fn=_note_fn)

    ai_text = await generate_risk_assessment(name, results, fused_op, decision_str)

    return _build_screening_response(
        name=name,
        entity_type=entity_type,
        country=country,
        results=results,
        evidence_opinions=opinions,
        evidence_dict=evidence_dict,
        fused_op=fused_op,
        decision_str=decision_str,
        conflict=conflict,
        outliers_removed=len(removed),
        top_score=top_score,
        start_time=start_time,
        ai_assessment=ai_text,
    )


# ---------------------------------------------------------------------------
# GET /api/screen/decay — screening with temporal decay
# ---------------------------------------------------------------------------


@app.get("/api/screen/decay")
async def screen_with_decay(
    name: str = Query(..., min_length=1, max_length=256),
    country: str = Query(default="Unknown"),
    entity_type: str = Query(default="individual"),
    days_since_screening: int = Query(default=0, ge=0),
) -> ScreeningResponse:
    """Run a compliance screening with temporal decay applied."""
    start_time = time.time()

    results, sdn_available = await query_sdn(name)

    # --- SDN unavailable: vacuous opinions → GATHER_MORE ---
    if not sdn_available:
        vacuous = Opinion(belief=0.0, disbelief=0.0, uncertainty=1.0)
        opinions = {k: vacuous for k in _DIMENSION_KEYS}
        evidence_dict = _build_evidence_dict(
            opinions, note_fn=lambda _k: "SDN API unavailable"
        )
        fused_op = vacuous
        if days_since_screening > 0:
            fused_op = apply_decay(fused_op, days_since_screening)
        conflict = 0.0
        outliers_removed = 0
        decision_str = decide(fused_op)
        ai_text = await generate_risk_assessment(name, results, fused_op, decision_str)
        return _build_screening_response(
            name=name,
            entity_type=entity_type,
            country=country,
            results=results,
            evidence_opinions=opinions,
            evidence_dict=evidence_dict,
            fused_op=fused_op,
            decision_str=decision_str,
            conflict=conflict,
            outliers_removed=outliers_removed,
            top_score=None,
            start_time=start_time,
            ai_assessment=ai_text,
        )

    # --- Zero hits: no-match fast path ---
    if len(results) == 0:
        no_match = Opinion(belief=0.02, disbelief=0.78, uncertainty=0.20)
        opinions = {k: no_match for k in _DIMENSION_KEYS}
        evidence_dict = _build_evidence_dict(
            opinions, note_fn=lambda _k: "No SDN matches found"
        )
        fused_op = no_match
        if days_since_screening > 0:
            fused_op = apply_decay(fused_op, days_since_screening)
        conflict = 0.0
        outliers_removed = 0
        decision_str = decide(fused_op)
        ai_text = await generate_risk_assessment(name, results, fused_op, decision_str)
        return _build_screening_response(
            name=name,
            entity_type=entity_type,
            country=country,
            results=results,
            evidence_opinions=opinions,
            evidence_dict=evidence_dict,
            fused_op=fused_op,
            decision_str=decision_str,
            conflict=conflict,
            outliers_removed=outliers_removed,
            top_score=0.0,
            start_time=start_time,
            ai_assessment=ai_text,
        )

    # --- Hits exist: decompose, fuse, decay, decide ---
    top_match = results[0]
    top_score = top_match.get("score", 0.0)

    opinions = decompose_evidence(
        top_match=top_match,
        all_matches=results,
        screened_country=country,
        screened_type=entity_type,
    )

    opinion_list = list(opinions.values())
    fused_op, removed = fuse_opinions(opinion_list)
    conflict = compute_conflict(opinion_list)

    # Apply decay BEFORE deciding
    if days_since_screening > 0:
        fused_op = apply_decay(fused_op, days_since_screening)

    decision_str = decide(fused_op)

    def _note_fn(key: str) -> str:
        if key == "name_similarity":
            return f"SDN score: {top_score}"
        if key == "entity_type":
            return f"Screened: {entity_type}, SDN: {top_match.get('type', 'N/A')}"
        if key == "geography":
            return f"Screened: {country}, SDN: {top_match.get('country', 'N/A')}"
        if key == "program_severity":
            return f"Program: {top_match.get('program', 'N/A')}"
        if key == "alias_coverage":
            return f"{len(results)} SDN hit(s)"
        return ""

    evidence_dict = _build_evidence_dict(opinions, note_fn=_note_fn)

    ai_text = await generate_risk_assessment(name, results, fused_op, decision_str)

    return _build_screening_response(
        name=name,
        entity_type=entity_type,
        country=country,
        results=results,
        evidence_opinions=opinions,
        evidence_dict=evidence_dict,
        fused_op=fused_op,
        decision_str=decision_str,
        conflict=conflict,
        outliers_removed=len(removed),
        top_score=top_score,
        start_time=start_time,
        ai_assessment=ai_text,
    )
