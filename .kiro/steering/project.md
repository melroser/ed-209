# Project: ED-209

## Project Name
This project is called ED-209 (reference: RoboCop, 1987).
- Display name in UI: ED-209
- Python module name: ed209
- Package/CLI name: ed-209
- Do NOT use "ComplianceOS" anywhere — that name has been retired.

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

## MCP Tools Available
The jsonld-ex MCP server is running. When writing or verifying compliance math, 
call these MCP tools directly to validate logic before writing code:

- `create_opinion` — create Opinion(belief, disbelief, uncertainty)
- `robust_fuse` — fuse list of opinions with outlier removal, returns (fused, removed_list)
- `cumulative_fuse` — fuse two independent opinions
- `pairwise_conflict` — takes TWO opinions, returns float conflict score
- `conflict_metric` — internal conflict of a single opinion
- `decay_opinion` — takes opinion + elapsed_seconds + half_life_seconds (NOT days)
- `combine_opinions_from_scalars` — convert raw SDN scores to fused opinion directly

IMPORTANT API NOTES:
- robust_fuse returns a TUPLE: (Opinion, list_of_removed). Unpack it: fused, removed = robust_fuse(ops)
- decay_opinion uses SECONDS not days: elapsed_seconds=days*86400, half_life_seconds=14*86400
- pairwise_conflict takes exactly TWO opinions. To get max conflict across 5 dimensions, 
  compute all pairwise combinations and take the max.
