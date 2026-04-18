"""ED-209 — Uncertainty-aware OFAC sanctions screening backend."""

import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from itertools import combinations
from jsonld_ex import Opinion, robust_fuse, pairwise_conflict
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
