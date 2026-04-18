# Requirements Document

## Introduction

ED-209 is a compliance screening system that checks entities (individuals and organizations) against sanctions lists and produces risk assessments using Subjective Logic. The system combines evidence from multiple sources — SDN (Specially Designated Nationals) list lookups and AI-powered analysis — fuses them into a unified Opinion, and renders a decision (AUTO_CLEAR, ESCALATE, AUTO_BLOCK, or GATHER_MORE). A single-page frontend allows analysts to submit screening requests and review results.

## Glossary

- **Screening_Engine**: The backend system that orchestrates compliance screening workflows
- **SDN_Client**: The component that queries the SDN OpenAPI for sanctions matches
- **AI_Analyst**: The component that uses Anthropic Claude to generate natural language risk assessments and produce evidence Opinions
- **Opinion**: A Subjective Logic triple (belief, disbelief, uncertainty) where b + d + u = 1, representing an evidence-based assessment
- **Fusion_Engine**: The component that combines multiple Opinions using cumulative fusion from jsonld-ex
- **Decision_Engine**: The component that maps a fused Opinion to one of four outcomes
- **Entity**: A person or organization being screened
- **Analyst**: A human compliance officer using the system
- **Frontend**: The single-page HTML interface for submitting and reviewing screenings

## Requirements

### Requirement 1: Submit Screening Request

**User Story:** As an Analyst, I want to submit an entity name for compliance screening, so that I can determine whether the entity poses a sanctions risk.

#### Acceptance Criteria

1. WHEN an Analyst submits an entity name via the Frontend, THE Screening_Engine SHALL accept the request and return a unique screening identifier
2. WHEN a screening request is received with an empty entity name, THE Screening_Engine SHALL return a validation error with a descriptive message
3. THE Screening_Engine SHALL accept entity names containing between 1 and 256 characters

### Requirement 2: SDN List Lookup

**User Story:** As an Analyst, I want the system to check entities against the SDN sanctions list, so that I can identify known sanctioned parties.

#### Acceptance Criteria

1. WHEN a screening is initiated, THE SDN_Client SHALL query the SDN OpenAPI with the entity name
2. WHEN the SDN OpenAPI returns results, THE SDN_Client SHALL convert each match into an Opinion based on the match score
3. WHEN the SDN OpenAPI returns a match with score >= 0.95, THE SDN_Client SHALL produce an Opinion with belief >= 0.85
4. WHEN the SDN OpenAPI returns a match with score < 0.5, THE SDN_Client SHALL produce an Opinion with uncertainty >= 0.6
5. IF the SDN OpenAPI is unreachable or returns an error, THEN THE SDN_Client SHALL produce an Opinion with uncertainty = 1.0 (vacuous opinion) and log the failure

### Requirement 3: AI Risk Assessment

**User Story:** As an Analyst, I want AI-generated risk analysis for screened entities, so that I can understand contextual risk factors beyond list matching.

#### Acceptance Criteria

1. WHEN a screening is initiated, THE AI_Analyst SHALL send the entity name and any SDN matches to Anthropic Claude for risk assessment
2. WHEN Claude returns a risk assessment, THE AI_Analyst SHALL produce an Opinion reflecting the assessed risk level
3. WHEN Claude indicates high confidence of risk, THE AI_Analyst SHALL produce an Opinion with belief >= 0.7
4. WHEN Claude indicates low risk, THE AI_Analyst SHALL produce an Opinion with disbelief >= 0.7
5. IF the Anthropic API is unreachable or returns an error, THEN THE AI_Analyst SHALL produce a vacuous Opinion with uncertainty = 1.0 and log the failure

### Requirement 4: Evidence Fusion

**User Story:** As an Analyst, I want evidence from multiple sources combined into a single assessment, so that I can make decisions based on all available information.

#### Acceptance Criteria

1. WHEN Opinions from SDN_Client and AI_Analyst are available, THE Fusion_Engine SHALL combine them using cumulative_fuse from jsonld-ex
2. THE Fusion_Engine SHALL produce a single fused Opinion representing the combined evidence
3. WHEN only one source produces a valid (non-vacuous) Opinion, THE Fusion_Engine SHALL use that Opinion as the fused result
4. THE Fusion_Engine SHALL preserve the constraint that belief + disbelief + uncertainty = 1 in the fused Opinion

### Requirement 5: Decision Rendering

**User Story:** As an Analyst, I want a clear decision outcome from the screening, so that I know what action to take.

#### Acceptance Criteria

1. WHEN a fused Opinion is produced, THE Decision_Engine SHALL map the Opinion to exactly one of: AUTO_CLEAR, ESCALATE, AUTO_BLOCK, or GATHER_MORE
2. WHEN the fused Opinion has belief >= 0.8, THE Decision_Engine SHALL render AUTO_BLOCK
3. WHEN the fused Opinion has disbelief >= 0.8, THE Decision_Engine SHALL render AUTO_CLEAR
4. WHEN the fused Opinion has uncertainty >= 0.7, THE Decision_Engine SHALL render GATHER_MORE
5. WHEN the fused Opinion does not meet any threshold for AUTO_BLOCK, AUTO_CLEAR, or GATHER_MORE, THE Decision_Engine SHALL render ESCALATE

### Requirement 6: Screening Results Display

**User Story:** As an Analyst, I want to view the full screening results including individual source Opinions, the fused Opinion, and the decision, so that I can understand how the conclusion was reached.

#### Acceptance Criteria

1. WHEN a screening completes, THE Frontend SHALL display the entity name, decision outcome, fused Opinion values, and individual source Opinions
2. WHEN a screening completes, THE Frontend SHALL display the AI-generated risk narrative
3. THE Frontend SHALL display Opinion values as percentages rounded to one decimal place
4. THE Frontend SHALL visually distinguish the four decision outcomes using color coding (green for AUTO_CLEAR, red for AUTO_BLOCK, yellow for ESCALATE, blue for GATHER_MORE)

### Requirement 7: API Endpoint Structure

**User Story:** As a developer, I want well-structured API endpoints, so that the frontend and any future integrations can interact with the system consistently.

#### Acceptance Criteria

1. THE Screening_Engine SHALL expose a POST /api/screen endpoint that accepts a JSON body with an entity name field
2. THE Screening_Engine SHALL expose a GET /api/screen/{screening_id} endpoint that returns the screening result
3. WHEN the POST /api/screen endpoint is called, THE Screening_Engine SHALL return the result synchronously in the response
4. THE Screening_Engine SHALL return all API responses as JSON with appropriate HTTP status codes (200 for success, 400 for validation errors, 500 for internal errors)

### Requirement 8: Opinion Serialization

**User Story:** As a developer, I want Opinions serialized in a consistent format, so that the frontend can reliably parse and display them.

#### Acceptance Criteria

1. THE Screening_Engine SHALL serialize Opinion objects as JSON with fields: belief, disbelief, uncertainty (each as a float between 0 and 1)
2. FOR ALL valid Opinion objects, serializing to JSON then deserializing SHALL produce an equivalent Opinion (round-trip property)
3. WHEN an Opinion is serialized, THE Screening_Engine SHALL validate that belief + disbelief + uncertainty equals 1.0 within a tolerance of 0.001
Here's everything, top to bottom, for a solo builder on a MacBook with `uv` and Kiro.

---

## Step 1: Install Kiro (2 min)

1. Go to [kiro.dev/downloads](https://kiro.dev/downloads)
2. Download the macOS `.dmg`
3. Drag to Applications, open it
4. Sign in with GitHub or Google (no AWS account needed)

---

## Step 2: Create the Project (3 min)

Open Terminal:

```bash
mkdir ~/ed-209 && cd ~/ed-209
uv init
uv add fastapi uvicorn httpx jsonld-ex anthropic
mkdir -p backend .kiro/steering
```

Open the `ed-209` folder in Kiro: **File → Open Folder → ~/ed-209**

---

## Step 3: Add a Steering File (1 min)

This tells Kiro your conventions. Create `.kiro/steering/project.md`:

```markdown
# Project: ED-209

## Stack
- Python 3.12+ with uv for dependency management
- FastAPI for the backend API
- jsonld-ex library for Subjective Logic (Opinion, cumulative_fuse, robust_fuse, decay_opinion, pairwise_conflict)
- httpx for async HTTP calls to external APIs
- Anthropic Claude API for AI text generation
- Frontend: single HTML file with inline CSS/JS (no framework, no build step)

## Conventions
- Use `uv run` to execute Python commands (not pip, not python directly)
- Backend code goes in `backend/` directory
- Frontend is a single `frontend/index.html` file
- All API endpoints are under `/api/`
- Use async/await for all HTTP calls
- Never use print() for logging — use Python logging module
- All Opinion objects use jsonld-ex: `from jsonld_ex import Opinion, cumulative_fuse`
- Do NOT write custom Subjective Logic math — use jsonld-ex library functions

## External APIs
- SDN OpenAPI: GET https://sdn-openapi.netlify.app/api/search?q={name}
  Returns: { results: [{ name, type, score, program, ... }] }
- Anthropic Claude: used for natural language risk assessments

## Key Domain Concepts
- An Opinion is (belief, disbelief, uncertainty) where b+d+u=1
- Cumulative fusion combines independent evidence about the same proposition
- Higher uncertainty = we need more evidence, NOT that something is risky
- Four decisions: AUTO_CLEAR, ESCALATE, AUTO_BLOCK, GATHER_MORE
```

---

## Step 4: Give Kiro the Spec Prompt

In Kiro's left panel, click the **`+`** button under **Specs**, choose **Feature**, select **Requirements-First**, and paste this:

---

> **Build ED-209: an uncertainty-aware OFAC sanctions screening tool that replaces binary pass/fail with Subjective Logic opinions.**
>
> **What it does:** A user enters a person or entity name. The system screens it against the live OFAC SDN sanctions list via the SDN OpenAPI (https://sdn-openapi.netlify.app/api/search), decomposes the screening into 5 independent evidence dimensions, models each as a Subjective Logic opinion using the jsonld-ex library, fuses them via cumulative fusion, and outputs one of four actionable decisions: AUTO_CLEAR, ESCALATE, AUTO_BLOCK, or GATHER_MORE. An AI agent (Claude) generates a 3-sentence natural language risk assessment. A single-page frontend visualizes everything.
>
> **The problem it solves:** Current sanctions screening tools produce a single fuzzy match score and compare it to a binary threshold. The industry false positive rate is 85-95%. This wastes billions in manual analyst review. Our system distinguishes between "strong match" (high belief, low uncertainty), "probably not a match but we don't have enough data" (low belief, high uncertainty), and "evidence conflicts" (high belief AND high disbelief) — three states that binary systems collapse into one.
>
> **Backend requirements:**
> - FastAPI server in backend/app.py
> - GET /api/screen?name={name}&country={country}&entity_type={type} endpoint
> - Calls SDN OpenAPI to get fuzzy matches
> - Decomposes the top match into 5 evidence opinions: name_similarity, entity_type, geography, program_severity, alias_coverage
> - Each opinion is a jsonld-ex Opinion(belief, disbelief, uncertainty) object
> - Name similarity: maps fuzzy score ranges to opinions (>=0.95 high belief, >=0.85 moderate, >=0.70 low, else very low)
> - Entity type: compares screened type vs SDN type. Match = moderate belief. Mismatch = strong disbelief. Missing = pure uncertainty.
> - Geography: compares screened country vs SDN country/address. Match = belief. Mismatch = disbelief. Missing = pure uncertainty.
> - Program severity: SDGT/WMD/IRAN/DPRK = higher belief. Other programs = lower. Missing = uncertainty.
> - Alias coverage: multiple hits with high scores = corroborating belief. Single weak hit = uncertainty.
> - Uses jsonld-ex robust_fuse() to fuse all 5 opinions with outlier removal
> - Uses jsonld-ex pairwise_conflict() to detect when evidence dimensions disagree
> - Classifies fused opinion into decision: AUTO_BLOCK (b>=0.6, u<=0.25), ESCALATE (b>=0.35, u>0.25), AUTO_CLEAR (d>=0.45, u<=0.35), else GATHER_MORE
> - Returns JSON with: entity, sdn_hits, sdn_results, evidence (per-dimension opinions), fused opinion, decision, conflict_score, binary_comparison (what a traditional system would decide), latency_ms
> - GET /api/screen/decay?name={name}&days_since_screening={days} — same as above but applies jsonld-ex decay_opinion() with exponential decay and 14-day half-life to show how old screenings go stale
> - GET /api/health returns {"status": "ok"}
> - Uses Anthropic Claude to generate a 3-sentence risk assessment for each screening. Graceful fallback if API is unavailable.
> - CORS enabled for all origins
>
> **Frontend requirements:**
> - Single file: frontend/index.html with inline CSS and JS (no build step, no framework)
> - Dark enterprise theme: background #06080d, cards #111827, borders #1e293b
> - Fonts: Inter for text, JetBrains Mono for numbers (loaded from Google Fonts CDN)
> - Header with "ED-209" logo and "Powered by Subjective Logic" badge
> - Search bar with magnifying glass icon, "Run Compliance Check" button
> - Two dropdowns: Country (US, UK, Germany, Israel, Turkey, Iran, Russia, China, Unknown) and Type (Individual, Entity, Vessel)
> - Four quick-search chip buttons: "Ed Sim", "Ayal Stern", "Leonard Tang", "Brian Brackeen"
> - Clicking a chip fills the search bar and triggers the search
> - Results section appears with fade-up animation after search
> - Status bar: colored pulsing dot (green/amber/red/blue), entity name, hit count, decision pill tag
> - Projected probability card: large monospace number colored by risk level, thin progress bar
> - 5 evidence cards in a grid, each showing: label, circular SVG ring gauge for belief value, three horizontal mini-bars for b/d/u, small monospace text showing input data
> - SDN matches table: entity name, type, program, match score with colored badges
> - AI Risk Assessment card with brain icon, purple badge, typing animation effect
> - CRITICAL: "Binary vs ED-209" comparison card at the bottom showing side-by-side: traditional system shows fuzzy score + "FLAGGED" in red vs our system shows fused opinion + decision in correct color. This is the visual punchline.
> - Footer: "Built on jsonld-ex · Subjective Logic (Jøsang 2016) · Live OFAC data via SDN OpenAPI"
> - Frontend calls backend at configurable BASE_URL (default http://localhost:8000)
> - Must work when opened directly in a browser (file:// or served via python -m http.server)
>
> **Demo scenarios to verify:**
> - "Ed Sim" with country=US, type=individual → SDN API returns fuzzy hits → after fusion, AUTO_CLEAR (type/geo mismatch kills the name similarity signal)
> - "Ayal Stern" with country=US, type=individual → SDN API returns fuzzy hit ~0.78 → after fusion, AUTO_CLEAR
> - "Leonard Tang" with country=US, type=individual → SDN API returns 0 hits → immediate AUTO_CLEAR
> - "Brian Brackeen" with country=US, type=individual → SDN API returns 0 hits → immediate AUTO_CLEAR
>
> **What NOT to build:**
> - No database, no auth, no user accounts
> - No Docker, no deployment configs
> - No React, no npm, no build step for frontend
> - No custom Subjective Logic math — use jsonld-ex library only

---

## Step 5: Walk Through Kiro's Flow

After you paste the prompt:

1. **Kiro generates `requirements.md`** — Review it. It should have user stories and acceptance criteria. If anything from the prompt is missing, type in the chat: *"Add a requirement for the binary vs ED-209 comparison card"* or whatever's missing.

2. **Click "Move to design phase"** — Kiro generates `design.md` with architecture, data flow, component design. Review it. Confirm it uses `jsonld-ex` imports and not custom math.

3. **Click "Move to implementation plan"** — Kiro generates `tasks.md` with sequenced, discrete tasks.

4. **Send me the contents of all three files** (`requirements.md`, `design.md`, `tasks.md`) and I'll verify before you execute.

---

## Step 6: Execute Tasks

Once I verify, for each task in `tasks.md`:

1. Click **"Start task"** above the task
2. Kiro writes the code
3. Review what it wrote — make sure it uses `from jsonld_ex import Opinion, cumulative_fuse, robust_fuse` etc.
4. If it tries to install dependencies, make sure it uses `uv add` not `pip install`
5. Move to next task

---

## Step 7: Run and Test

```bash
# Terminal 1 — Backend
cd ~/ed-209
uv run uvicorn backend.app:app --reload --port 8000

# Terminal 2 — Frontend
cd ~/ed-209/frontend
python3 -m http.server 3000

# Terminal 3 — Test
curl "http://localhost:8000/api/screen?name=Ed%20Sim&country=United%20States&entity_type=individual"
```

Open `http://localhost:3000/index.html` in Chrome. Click "Ed Sim" chip. Verify the full flow works.

---

## Step 8: Submit (by 5:55 PM)

```bash
cd ~/ed-209
git init && git add -A && git commit -m "ED-209 — uncertainty-aware sanctions screening"
# Create repo on GitHub, push
git remote add origin https://github.com/YOUR_USERNAME/ed-209.git
git push -u origin main
```

Make sure your README has:
* What it does (2 sentences)
* How to run it (3 commands)
* Screenshot of the Ed Sim demo
* Link to the compliance algebra paper
* Link to jsonld-ex on PyPI
* Link to sdn-openapi.netlify.app

---

## Quick Reference Card (tape this to your screen)

```
BACKEND:  uv run uvicorn backend.app:app --reload --port 8000
FRONTEND: cd frontend && python3 -m http.server 3000
TEST:     curl "localhost:8000/api/screen?name=Ed+Sim&country=United+States&entity_type=individual"
SDN API:  https://sdn-openapi.netlify.app/api/search?q=NAME
SUBMIT:   Git push + pitch deck by 5:55 PM
DEMO ORDER: Brackeen (clean) → Tang (clean) → Ed Sim (fuzzy→clear) → Ayal Stern (fuzzy→clear)
```

Go open Kiro, paste the prompt, and send me back what it generates. Clock's running. ⏱️

