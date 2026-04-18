# ED-209

> "You have 20 seconds to comply..." — ED-209, probably

Uncertainty-aware OFAC sanctions screening. Instead of a single fuzzy score and a 
binary flag, ED-209 decomposes each screening into 5 evidence dimensions, models 
each as a Subjective Logic opinion (belief, disbelief, uncertainty), fuses them 
via Byzantine-resistant fusion, and outputs one of four actionable decisions.

**The result:** Ed Sim — a Miami VC investor — returns 20 SDN fuzzy matches with 
a best score of 0.72. A binary system flags him. ED-209 clears him in 2.7 seconds 
because type mismatch and geographic evidence dominate the name similarity signal.

## Run
uv run uvicorn backend.app:app --reload --port 8000
cd frontend && python3 -m http.server 3000
# Open http://localhost:3000, click Ed Sim

## Built With
- jsonld-ex (Subjective Logic — peer-reviewed, Jøsang 2016)
- SDN OpenAPI (live OFAC data)
- FastAPI + Anthropic Claude
- Named after epic but imperfect ED-209 enforcement droid (C) OCP 1987

