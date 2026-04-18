"""ED-209 — Uncertainty-aware OFAC sanctions screening backend."""

import logging
from typing import Optional

from fastapi import FastAPI, Query
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
