# ED-209

> *"I'm sorry, this individual cannot be processed."* — ED-209, probably

Uncertainty-aware OFAC sanctions screening using Subjective Logic.
Replaces binary pass/fail with four actionable decisions based on formal opinion algebra.

## Why ED-209?

Binary compliance tools are like ED-209 in the boardroom scene — they make a decision with incomplete information and can't course-correct. This system models uncertainty explicitly so it knows when to escalate instead of pulling the trigger.

## Run

```bash
uv run uvicorn backend.app:app --reload --port 8000
cd frontend && python3 -m http.server 3000
```

## Built with

- [jsonld-ex](https://pypi.org/project/jsonld-ex/) (Subjective Logic engine, peer-reviewed)
- [SDN OpenAPI](https://sdn-openapi.netlify.app) (live OFAC data)
- FastAPI + Claude (Anthropic)
