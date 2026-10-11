# Final day (Sun 17:00–20:00 UTC): what changed and what to do

Source: the event page shown by the user at 16:59 UTC. It replaces the "two hidden briefs" picture in `plan.md`,
`CLAUDE.md` and `docs/runbook.md` wherever they disagree.

## The final case

- ONE case, delivered as a zip: a folder (example name `FreyaBank-Ekstre-Borc/`) with `BRIEF.md` (read first) and
  `README.md` (says what each document is): analysis, flow drawing, announcements, **service contract**,
  **test customers**, questions and answers, 6 example conversations. **All in Turkish.**
- Run command shape: `python3 builder/build.py {case_folder} {mock_url}` — the second argument is the URL of OUR
  mock, not a tool API file.
- We write a **mock of the bank's services on Cloudflare Workers** from the service contract and the test customers,
  deploy it before running the builder, and keep it running until results are out. The organisers' simulated
  customers talk to our agent in their workspace; the agent's tools call our mock.
- Submit ONE agent + the trace of the run that made it. `submission.json` (sent by the user before 20:00 UTC):

      {"team": "...", "members": [{"name": "...", "email": "..."}], "repo": "https://github.com/efeboyacim/thebuilder",
       "commit": "<pushed hash>", "run_command": "python3 builder/build.py {case_folder} {mock_url}",
       "workflow": "submission/final.workflow.json", "trace": "traces/final.log",
       "mock_url": "https://<our-mock>.workers.dev"}

- The 3 hours are for running the builder and fixing bugs, not tuning to the case. Nothing from the case may be
  written into the builder (the mock is separate and is ours to write from the contract).

## Gaps in our builder, in priority order (generic fixes only)

1. **CLI**: accept `<folder> <url>`. `ingest.py` already walks a directory (line ~105). Find the service contract
   in the folder: an `openapi` JSON/YAML if present; if the contract is prose, the tool API must be produced first
   (LLM: contract → OpenAPI with the seven tool names) — check the zip before deciding. The `<url>` argument
   overrides `servers[0].url` (toolapi.py `server`).
2. **Ingest formats**: check what the zip holds (md, txt, json, yaml, csv, pdf, png for the flow drawing?). Make
   sure every text document reaches the digest; Turkish text must stay UTF-8 (`PYTHONUTF8=1`).
3. **Turkish speech**: the agent must speak Turkish. Spec templates come from the LLM: the spec/digest prompts must
   say to write every customer-facing text in the language of the material. Engine literals that reach the
   customer: `CONTINUE = "Please go on."` (engine.py:25) and any other English the brain says directly — move
   them into the spec (phrases) or let the LLM translate at build time.
4. **Turkish numbers and dates**: `policy_lib` parses English number words, month names and weekdays only.
   Fastest generic fix: in `contract.py`, tell the extraction model to write amounts as digits, dates as
   day + month number (+ year) and times as HH:MM whatever language the customer uses; keep the code parsers
   for the normalised strings. Weekday words for relative dates also need the extractor to normalise
   (e.g. to an English weekday or an ISO date it must NOT compute — prefer English weekday words).
5. **Slot and outputs**: run with `--slot final` so the export is `submission/final.workflow.json`; tee the
   printout to `traces/final.log`.
6. **Self-test on our own mock**: the practice ledger does not exist for the final. Use the case's test customers
   against our mock: a short Freya simulation (`freya.run_simulation` with personas written from the test
   customers / example conversations) or the chat route; read calls with `tools/call_events.py`.

## The mock (Cloudflare Workers)

- `node`/`npx` are installed; use `npx wrangler` (no global wrangler). The USER must run `npx wrangler login`
  (browser) — Claude must not handle Cloudflare credentials.
- Write it in `mock/` (separate from `builder/`): one Worker implementing every operation of the service
  contract over the test customers, with in-memory/Durable-Object or KV state for recorded promises, notes,
  callbacks, transfers and close; same request/response shapes and error behaviour as the contract.
- Deploy with `npx wrangler deploy`, smoke-test each endpoint with curl, then run the builder with its URL.
