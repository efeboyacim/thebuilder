# Runbook: hidden-brief day (Sun 17:00–20:00 UTC)

Form run command (D-Z winner, our builder):

    python3 builder/build.py {brief_path} {tool_api_path}

Locally (Windows): `source env.sh && export PYTHONUTF8=1 && python builder/build.py <brief.md> <openapi.json>`.
Brief B on top of A: pass both briefs, A first. The tool API is found by content.

1. Run the builder on each hidden brief, keeping its printout (the form asks for it):
   `python builder/build.py <brief.md> <openapi.json> 2>&1 | tee traces/<stem>.log`.
   Run it (5.5–7.5 min each; the LLM spec is the slow part). Check the summary:
   `status ok`, workflow/agent ids, export `submission/<slot>.workflow.json`, trace `traces/<slot>-<time>.jsonl`.
2. If it stops with a model error (session limit, timeout): run it again; retries stop after 20 min of run time.
   A spec left with only indexing/quoting/wording errors is kept (warnings in the trace).
3. If the spec is valid but the agent misbehaves: bug fixes in `builder/` only, never a hidden-brief value in code or
   prompts; then `python -m pytest tests/unit -q` and `python tools/leak_lint.py` before rebuilding.
4. Commit `submission/*.workflow.json`, `traces/<stem>.log` and the `traces/*.jsonl` of the run that made each agent
   (nothing else from `out/`), **push** (origin has only the empty initial commit), then send the form by 19:45 UTC:

```json
{"team": "<team>", "repo": "https://github.com/efeboyacim/thebuilder", "commit": "<pushed hash>",
 "run_command": "python3 builder/build.py {brief_path} {tool_api_path}",
 "workflows": {"<hidden-1>": "submission/hub-<stem1>.workflow.json", "<hidden-2>": "submission/hub-<stem2>.workflow.json"},
 "traces": {"<hidden-1>": "traces/<stem1>.log", "<hidden-2>": "traces/<stem2>.log"}}
```
   The phone agent must not be edited after the run; a fix means a new builder commit and a new run.

Fallback (Plan Z, kit builder, blind): `python3 planz/build.py {brief_path} {tool_api_path}` (≈15 min, M1 8/13).
