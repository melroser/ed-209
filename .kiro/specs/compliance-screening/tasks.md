# Implementation Plan: ED-209 Compliance Screening

## Overview

Build a hackathon-grade OFAC sanctions screening system with a FastAPI backend (`backend/app.py`), Subjective Logic evidence fusion via jsonld-ex, Claude-powered risk narratives, and a single-file dark-themed frontend (`frontend/index.html`). Three top-level tasks: backend core, AI analyst, frontend.

## Tasks

- [ ] 1. Backend core — SDN client, evidence decomposer, fusion, decision, API endpoints
  - [x] 1.1 Create `backend/__init__.py` and `backend/app.py` with FastAPI app, CORS middleware, Pydantic response models, and `/api/health` endpoint
    - Define all Pydantic models: `EvidenceOpinion`, `FusedOpinion`, `DecisionResult`, `SDNMatch`, `BinaryComparison`, `ScreeningResponse`
    - Add CORS middleware allowing all origins
    - Implement `GET /api/health` returning `{"status": "ok"}`
    - _Requirements: 7.1, 7.4, 8.1, 8.3_

  - [x] 1.2 Implement SDN client function `query_sdn(name: str) -> tuple[list[dict], bool]`
    - Use `httpx.AsyncClient` with 10-second timeout to call `https://sdn-openapi.netlify.app/api/search?q={name}`
    - Parse response JSON, return `(results, True)` on success
    - On error/timeout: log via `logging` module, return `([], False)` — caller MUST distinguish "no matches" from "API down"
    - CRITICAL: `sdn_available=True, results=[]` → no-match fast path (AUTO_CLEAR). `sdn_available=False` → vacuous opinions (0,0,1) → GATHER_MORE. If you conflate these, a downed API auto-clears everyone.
    - _Requirements: 2.1, 2.4_

  - [x] 1.3 Implement evidence decomposer `decompose_evidence(top_match, all_matches, screened_country, screened_type) -> dict[str, Opinion]`
    - `name_similarity`: score >= 0.95 → (0.80, 0.05, 0.15); >= 0.85 → (0.55, 0.15, 0.30); >= 0.70 → (0.30, 0.25, 0.45); < 0.70 → (0.15, 0.35, 0.50)
    - `entity_type`: match → (0.50, 0.10, 0.40); mismatch → (0.05, 0.70, 0.25); missing → (0.0, 0.0, 1.0)
    - `geography`: match → (0.60, 0.05, 0.35); mismatch → (0.05, 0.65, 0.30); missing → (0.0, 0.0, 1.0)
    - `program_severity`: SDGT/WMD/NPWMD/IRAN/DPRK/SYRIA → (0.30, 0.10, 0.60); other → (0.15, 0.15, 0.70); missing → (0.0, 0.0, 1.0)
    - `alias_coverage`: 3+ hits AND 2nd score > 0.75 → (0.55, 0.10, 0.35); 2+ hits AND 2nd score > 0.60 → (0.35, 0.20, 0.45); else → (0.15, 0.25, 0.60); zero hits → (0.0, 0.70, 0.30)
    - All Opinions created via `from jsonld_ex import Opinion`
    - _Requirements: 2.2, 2.3, 2.4_

  - [x] 1.4 Implement fusion engine and conflict scoring
    - `fuse_opinions(opinions: list[Opinion]) -> tuple[Opinion, list]` using `robust_fuse(opinions)` — MUST unpack tuple: `fused, removed = robust_fuse(opinions)`
    - `compute_conflict(opinions: list[Opinion]) -> float` using `pairwise_conflict(a, b)` on all `itertools.combinations(opinions, 2)`, return `max()`
    - Handle edge case: fewer than 2 opinions → conflict = 0.0
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [~] 1.5 Implement decision engine `decide(opinion: Opinion) -> str`
    - Evaluate in order: AUTO_BLOCK (b >= 0.6 AND u <= 0.25) → ESCALATE (b >= 0.35 AND u > 0.25) → AUTO_CLEAR (d >= 0.45 AND u <= 0.35) → GATHER_MORE (fallback)
    - Returns exactly one of the four decision strings
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [~] 1.6 Implement binary comparison `binary_decision(top_score: float | None) -> dict`
    - Threshold = 0.65; score >= threshold → "FLAGGED", else → "CLEARED"
    - None score → score = 0.0, decision = "CLEARED"
    - _Requirements: 6.1_

  - [~] 1.7 Implement decay function `apply_decay(opinion: Opinion, days: int) -> Opinion`
    - Convert days to seconds: `elapsed_seconds = days * 86400`
    - Half-life = 14 days = 1,209,600 seconds
    - Call with KEYWORD ARGS: `decay_opinion(opinion, elapsed_seconds=days * 86400, half_life_seconds=1_209_600)` — positional args are fragile if library adds params
    - _Requirements: 12.2, 12.3_

  - [~] 1.8 Implement `GET /api/screen` endpoint — the main screening orchestrator
    - Accept query params: `name` (required, 1-256 chars), `country` (default "Unknown"), `entity_type` (default "individual")
    - Call `query_sdn(name)` → returns `(results, sdn_available)`
    - `sdn_available=False` → use vacuous opinions `(0, 0, 1)` for all 5 dimensions → decision will be GATHER_MORE (API down, never auto-clear)
    - `sdn_available=True`, zero hits → use pre-built no-match Opinion `(0.02, 0.78, 0.20)` for all 5 dimensions, skip decomposition, decision = AUTO_CLEAR
    - Hits exist → call `decompose_evidence()` with top match, then `fuse_opinions()` and `compute_conflict()`
    - Call `decide()` on fused opinion, map decision to color (green/red/amber/blue) and label
    - Call `binary_decision()` with top match score
    - Compute `projected` using SL formula: `round((b + 0.5 * u) * 100, 1)` — NOT just `b * 100`
    - Build and return `ScreeningResponse` matching the exact JSON contract
    - Measure latency with `time.time()` and include `latency_ms`
    - _Requirements: 1.1, 1.2, 1.3, 2.3, 2.4, 9.1-9.5, 10.1-10.3, 17.1-17.3_

  - [~] 1.9 Implement `GET /api/screen/decay` endpoint
    - Same as `/api/screen` but accepts additional `days_since_screening` query param
    - After computing fused opinion, apply `apply_decay()` before deciding
    - _Requirements: 7.1_

  - [ ]* 1.10 Write property tests for opinion validity, decision completeness, and score-to-opinion mapping
    - **Property 1: Opinion validity invariant** — for any decomposer inputs, all produced Opinions satisfy b+d+u=1 (±0.001) and each component in [0,1]
    - **Validates: Requirements 2.2, 3.2, 4.2, 4.4, 8.3**
    - **Property 3: Score-to-Opinion mapping respects thresholds** — for any score in [0,1], name_similarity opinion satisfies: score >= 0.95 → belief >= 0.85; score < 0.5 → uncertainty >= 0.6
    - **Validates: Requirements 2.3, 2.4**
    - **Property 4: Decision engine completeness and correctness** — for any valid Opinion, decide() returns exactly one of the four decisions and respects all threshold rules
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**

  - [ ]* 1.11 Write property tests for serialization round-trip and vacuous opinion passthrough
    - **Property 2: Opinion serialization round-trip** — for any valid Opinion, serialize to JSON then deserialize produces equivalent Opinion (±0.001)
    - **Validates: Requirements 8.1, 8.2**
    - **Property 6: Vacuous opinion passthrough in fusion** — fusing a non-vacuous Opinion with vacuous Opinions yields the non-vacuous Opinion
    - **Validates: Requirements 4.3**

- [~] 2. Checkpoint — Verify backend core
  - Ensure all tests pass, ask the user if questions arise.
  - Run: `uv run uvicorn backend.app:app --reload --port 8000` and test with `curl "http://localhost:8000/api/screen?name=Ed+Sim&country=United+States&entity_type=individual"`

- [ ] 3. AI analyst integration — Claude risk narratives
  - [~] 3.1 Implement `generate_risk_assessment(entity_name, sdn_matches, fused_opinion, decision) -> str`
    - Use `anthropic.AsyncAnthropic()` client with httpx
    - Prompt Claude for a 3-sentence risk narrative given the entity, SDN matches, fused opinion values, and decision
    - Returns plain text string — NO Opinion object
    - On API error/timeout: log error, return fallback string "AI risk assessment unavailable."
    - _Requirements: 3.1, 3.5_

  - [~] 3.2 Wire AI analyst into the `/api/screen` endpoint
    - Call `generate_risk_assessment()` AFTER SDN results are available — Claude needs the matches as input, so you CANNOT parallelize SDN + AI with asyncio.gather()
    - You CAN call Claude after SDN returns but concurrently with local fusion/decision computation if desired
    - Include the `ai_assessment` string in the `ScreeningResponse`
    - _Requirements: 11.1, 11.2_

- [ ] 4. Frontend — single-page dark-themed UI
  - [~] 4.1 Create `frontend/index.html` with HTML structure, dark theme CSS, and search form
    - Dark enterprise theme: background #06080d, cards #111827, borders #1e293b
    - Load Inter + JetBrains Mono from Google Fonts CDN
    - Header with "ED-209" logo and "Powered by Subjective Logic" badge
    - Search bar with magnifying glass icon, country dropdown (US, UK, Germany, Israel, Turkey, Iran, Russia, China, Unknown), type dropdown (Individual, Entity, Vessel)
    - "Run Compliance Check" button
    - Four quick-search chip buttons: "Ed Sim", "Ayal Stern", "Leonard Tang", "Brian Brackeen" — default country="United States", type="Individual"
    - Clicking a chip fills the search bar and triggers the search
    - _Requirements: 1.1, 6.1, 6.4_

  - [~] 4.2 Implement results display: status bar, projected probability, evidence cards, SDN table, AI assessment, binary comparison
    - Status bar: colored pulsing dot (green/amber/red/blue), entity name, hit count, decision pill tag
    - Projected probability card: large monospace number colored by risk level, thin progress bar
    - 5 evidence cards in a grid: label, circular SVG ring gauge for belief, three horizontal mini-bars for b/d/u, small monospace text showing input data
    - SDN matches table: entity name, type, program, match score with colored badges
    - AI Risk Assessment card with brain icon, purple badge, typing animation effect
    - "Binary vs ED-209" comparison card: traditional system shows fuzzy score + "FLAGGED" in red vs ED-209 shows fused opinion + decision in correct color
    - Opinion values displayed as percentages rounded to one decimal place
    - Results appear with fade-up animation after search
    - Footer: "Built on jsonld-ex · Subjective Logic (Jøsang 2016) · Live OFAC data via SDN OpenAPI"
    - Configurable `BASE_URL` (default `http://localhost:8000`)
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 4.3 Write property test for percentage formatting
    - **Property 7: Percentage formatting correctness** — for any float in [0,1], formatting as percentage produces `round(value * 100, 1)`
    - **Validates: Requirements 6.3**

- [~] 5. Final checkpoint — End-to-end verification
  - Ensure all tests pass, ask the user if questions arise.
  - Verify demo scenarios: "Ed Sim" → AUTO_CLEAR, "Leonard Tang" → AUTO_CLEAR (zero hits), "Brian Brackeen" → AUTO_CLEAR (zero hits)

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- All code uses `from jsonld_ex import Opinion, cumulative_fuse, robust_fuse, pairwise_conflict, decay_opinion` — no custom SL math
- `robust_fuse()` returns a tuple `(fused, removed)` — always unpack
- `pairwise_conflict(a, b)` takes exactly TWO opinions — use `itertools.combinations` for multiple, return `max()`
- `decay_opinion()` uses SECONDS not days — use KEYWORD ARGS: `elapsed_seconds=days*86400, half_life_seconds=14*86400`
- AI analyst returns text only, never an Opinion object
- `query_sdn()` returns `(results, sdn_available)` — MUST distinguish API-down from zero-matches
- `projected` uses SL formula: `round((b + 0.5 * u) * 100, 1)` — NOT just `b * 100`
- Run backend with `uv run uvicorn backend.app:app --reload --port 8000`
- Run frontend with `cd frontend && python3 -m http.server 3000`
