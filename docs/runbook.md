# Runbook: hidden-brief day (Sun 17:00–20:00 UTC)

Form run command (D-Z winner, our builder):

    python3 builder/build.py {brief_path} {tool_api_path}

Locally (Windows): `source env.sh && export PYTHONUTF8=1 && python builder/build.py <brief.md> <openapi.json>`.
Brief B on top of A: pass both briefs, A first. The tool API is found by content.

1. Run the builder on each hidden brief (5.5–7.5 min each; the LLM spec is the slow part). Check the summary:
   `status ok`, workflow/agent ids, export `submission/<slot>.workflow.json`, trace `traces/<slot>-<time>.jsonl`.
2. If it stops with a model error (session limit, timeout): run it again; retries stop after 20 min of run time.
   A spec left with only indexing/quoting/wording errors is kept (warnings in the trace).
3. If the spec is valid but the agent misbehaves: bug fixes in `builder/` only, never a hidden-brief value in code or
   prompts; then `python -m pytest tests/unit -q` and `python tools/leak_lint.py` before rebuilding.
4. Commit the exports and traces, then send the form by 19:45 UTC.

Fallback (Plan Z, kit builder, blind): `python3 planz/build.py {brief_path} {tool_api_path}` (≈15 min, M1 8/13).
