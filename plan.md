# Plan: Freya "Build the Builder"

## Goal and architecture

Goal: `python3 builder/build.py {brief_path} {tool_api_path}` reads one Markdown brief (a clean spec, or raw material
with a mail, policy extract, amending memo and code list as sections) plus its tool API file, and creates a Freya
phone agent. Several brief files occur only for Brief B on top of A. The agent must pass hidden customers 3/3 on
several calendar days, with no hand edits, in ≤30 min per brief.

Architecture, in five lines (details in [docs/design.md](docs/design.md)):

1. **Hybrid compiler.** Claude reads the brief material into a validated JSON spec. The tool API is parsed from the
   openapi file without an LLM. Python compiles spec + API into the workflow. No LLM writes workflow JSON.
2. **Generated agent = hub-and-spoke "brain".** One generic, spec-parameterised Python policy engine in a code node
   decides everything. Talk nodes only extract the turn and speak `say_next` by tts. Writes are engine-run api nodes.
   `close_call` is a lifecycle tool with a code outcome. Flow rules nobody modelled go to a free-mode llm stage, and
   code gates every write.
3. **Nothing brief-specific in the builder.** A binding layer maps engine roles to openapi fields. Situations are
   open records. Every switch is required, with an explicit `not_specified` and no Brief A defaults. A lint checks
   for A/B literals.
4. **Self-tests in ≤30 min:** lint and graph checks, an offline stub bank built from the openapi file, local replay
   through the generated code, Freya dry run, save, read-back and diff, and turn-by-turn chat with an LLM customer
   through the chat API: the only behavioural test on a hidden brief (no practice cases, no own customers).
5. **Safety:** gate G1 (platform spikes), then a walking skeleton (K1 at Sat 21:00), live mock rehearsals on reused
   PUB customer ids, and Plan Z (the kit's agentic builder, no shared code) until decision D-Z at Sun 13:00.

## Timeline

All times UTC (Istanbul = UTC+3). About 20 working hours, with a sleep block. Backlog numbers follow this order.

| By | Milestone |
|---|---|
| Sat 13:15 | 01 setup verified |
| Sat 15:30 | **G1**: 03–04 spikes (chat API only) decide hub / L1 / L2 / L3. 02 Plan Z builds run meanwhile and use the simulation queue for their own tests |
| Sat 20:30 | **Walking skeleton** 05–09: PUB-01, PUB-02, PUB-03 pass live |
| Sat 21:00 | **K1**: skeleton red → switch to L3 now. 10 starts the overnight queue |
| Sat 21:00–Sun 03:00 | **Sleep**; the overnight queue runs unattended |
| Sun 03:15 | Overnight scores recorded |
| Sun 06:00 | 11 dates and ladder, 12 amounts, events and side questions (live `--only` runs each) |
| Sun 08:30 | 13 full A, 14 B from LLM-built specs; 15 and 16 written while simulations run |
| Sun 10:00 | 17 M2 written, 18 free mode; 19's blind Plan Z build on M1 runs in the background (no simulations) |
| Sun 10:00–10:30 | **User reviews M1's expected ledgers** (reserved) |
| Sun 12:15 | D-Z inputs first: 19 Plan Z on M1 `--reps 3`, then 21 ours on M1 `--reps 3`; 20 raw digest and M2 spec diff in between |
| Sun 13:00 | **D-Z**: one builder for both hidden briefs |
| Sun 15:00 | 22 styles (queue); 23 self-tests and 24 M3 written meanwhile |
| Sun 15:45 | 25 hardening |
| Sun 16:45 | **Freeze** (26) |
| Sun 17:00–20:00 | 27 hidden runs; bug fixes only; form sent by 19:45 |

## Backlog

- [ ] 04 Spikes S2, S2b, S3 (PUB-01 and PUB-10 practice sessions). Covers: api bodies baked from code vars, key naming (node ≠ function), amount type, mapped product list form, failure envelope and stale keys (01 saw `ok:false` "service unavailable" on create_promise, not an HTTP 500), lifecycle close_call with sentinel reason and pre-hook, unresolved reason, close on hang-up. Acceptance: `python3 spikes/s2_api.py` → bodies printed; ledger shows exactly 1 promise, a numeric amount, reason null; G1 decision recorded in Decisions.
- [ ] 05 Builder skeleton. `builder/build.py` CLI (`--slot`, `BUILDER_SLOT`, `--until`, `--spec`), ingest of file paths only (one Markdown brief per hidden brief; several brief files only because Brief B builds on A, kept in the given order; no directory ingest), classification by content (the tool API is the JSON with an `openapi` key), `builder/trace.py` with redaction, `builder/llm.py` adapter (API key first, Agent SDK fallback). Tests live in the top-level `tests/unit/`. Acceptance: `python3 builder/build.py briefs/brief-a.md tools/openapi.json --until ingest` and `python3 builder/build.py tools/openapi.json briefs/brief-a.md briefs/brief-b.md --until ingest` → print the classified inputs (briefs in order, one tool API) and write a trace with run_start/run_end; `python3 -m pytest tests/unit -q` → passes.
- [ ] 06 ToolAPI, bindings model, stub bank. Parse request and response schemas; generate a stub bank that validates request bodies and serves schema-shaped responses, including hint, wrong-answer, `ok:false` and 500 paths. Acceptance: `python3 -m pytest tests/unit/test_toolapi.py -q` → parses `tools/openapi.json` and a renamed-field copy; the stub rejects a body missing a required field.
- [ ] 07 Spec v0. Schema sections the slice needs (bindings, opening, third party, identity, disclosure, first ask, confirm, situations, closing) in final generic form with `not_specified`; LLM digest and spec calls; deterministic validator; `tools/leak_lint.py`. Acceptance: `python3 builder/build.py briefs/brief-a.md tools/openapi.json --until spec` → spec validates in ≤6 min; `python3 tools/leak_lint.py` → clean.
- [ ] 08 policy_lib core + slice engine. Calendar basics, normalizers with completeness checks, opening, third party, identity, disclosure, pay today in full, read-back, record, close. Acceptance: `python3 -m pytest tests/unit/test_engine_slice.py -q` → PUB-01, PUB-02 and PUB-03 act-scripts reach their expected ledgers through the stub bank.
- [ ] 09 Compiler slice, save, export, trace. If 07 is late, compile a hand-written fixture with `--spec tests/fixtures/spec-a-slice.json` (test data only); K1 judges the hub runtime on Freya, not the extractor. Acceptance: `python3 builder/build.py briefs/brief-a.md tools/openapi.json --slot hub-a` then `python3 tools/run_cases.py <agent_id> --only PUB-01,PUB-02,PUB-03` → 3/3 by Sat 20:30 (K1 at 21:00).
- [ ] 10 Overnight queue. `tools/overnight.sh` runs these in sequence, unattended, each with `--out results/overnight-*.json`: Plan Z A agent `--brief A --reps 3`; Plan Z A+B agent `--brief B --reps 3`; hub slice `--only PUB-01,PUB-02,PUB-03 --reps 3`. Acceptance: `bash tools/overnight.sh` started before Sat 21:00 → at Sun 03:15 all three result files exist and the scores are recorded in Done.
- [ ] 11 Dates and persuasion ladder. Bands, deadline expressions, date parts resolved against `now`, holiday shift, vague dates, after-deadline offer, ladder with per-product offer record, promise retry; extend the spec schema, digest/spec prompts and validator for `calendar`, `products.bands`, `negotiation.ladder` and `failure_policy`. Acceptance: `python3 -m pytest tests/unit/test_dates.py -q` → green, including Christmas and New Year weeks, Fridays, weekends and after-hours `now`; and `python3 tools/run_cases.py <agent_id> --only PUB-07,PUB-10` → 2/2.
- [ ] 12 Amounts, events, side questions. Floor policies, hardship precedence and suppression, paid, dispute, restructuring, human, no-talk (`min_occurrences`), robot/scam side questions, llm twins for `side_q=other` with the pre-verification facts guard, loop guards; extend the spec schema, prompts and validator for `negotiation` amounts, `events[]`, `situations[]` and `phrases`. Acceptance: `python3 tools/run_cases.py <agent_id> --only PUB-04,PUB-05,PUB-06,PUB-08,PUB-09` → 5/5.
- [ ] 13 Full Brief A from an LLM-built spec. Fix loop until stable. Acceptance: `python3 builder/build.py briefs/brief-a.md tools/openapi.json --slot hub-a` (no `--spec`) then `python3 tools/run_cases.py <agent_id> --brief A --reps 3` → 10/10.
- [ ] 14 Multi-product Brief B. Extend the spec and engine for notes, multi-product flow and `closing.aggregation`; write `tests/cases/a-under-b.json` (A cases plus the B8 notes). Acceptance: `python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json --slot hub-ab` then `python3 tools/run_cases.py <agent_id> --brief B --reps 3` → 3/3, and `python3 tools/run_cases.py <agent_id> --cases tests/cases/a-under-b.json --brief A` → 10/10.
- [ ] 15 `tools/rehearse.py`. Runs custom case files on PUB customer ids, with our own scripts, checks and expected ledgers, and an optional `now` override per case. Acceptance: `python3 tools/rehearse.py <agent_id> --cases tests/cases/pub01-friday.json` → PASS for PUB-01 with `now` on a Friday and matching expectations.
- [ ] 16 Mock M1 (never cut). `mocks/m1/m1.md` + `mocks/m1/openapi.json`: a clean mutated A+B brief with case scripts on PUB customer ids over several `now` days. Mutations: other holidays, a calendar-day deadline, bands, attempts, callback window. Outcome/reason codes stay the mock's, because it rejects others (see Decisions). It includes one flow rule outside the schema (a weekly payday sets the attempt-1 date) with a case that depends on it. Expected ledgers come from a separate LLM pass over the M1 text (`tools/expected_from_brief.py`, imports nothing from `builder/`), never from our engine. Acceptance: `python3 tools/expected_from_brief.py mocks/m1/` → `mocks/m1/cases.json` written; the user approves it in the reserved review (Sun 10:00–10:30).
- [ ] 17 Mock M2 material. `mocks/m2/m2.md`: M1 rewritten as one Markdown file with a mail, a policy extract, an amending memo and a code list as sections, plus `mocks/m2/openapi.json`. Acceptance: `python3 builder/build.py mocks/m2/m2.md mocks/m2/openapi.json --until ingest` → one brief and one tool API classified, and the brief's four sections listed.
- [ ] 18 Free mode and residual classification. Speech/flow classes; flow residuals become events that route to `free_<stage>` (two-miss entry only in stages with a flow residual); code gates on every proposed write. Acceptance: `python3 -m pytest tests/unit/test_free_mode.py -q` → the payday-weekday scenario records the gated promise, and a seeded invalid action (wrong product id, date past the deadline) is rejected.
- [ ] 19 Plan Z blind. `planz/build.py {brief_path} {tool_api_path}` wraps the kit builder with a blind skill copy and runs it in a clean working copy: a fresh temp directory with only the kit files the skill needs (`docs/`, `freya.py`, `examples/base-agent.json`, the given brief and tool API), and no `CLAUDE.md`, `plan.md`, `results/`, `out/`, `traces/` or PUB cases (02 showed the kit builder reads them and reuses `out/gen_brief_a.py`). It accepts several brief files (B on A), finds the openapi by content, and runs no PUB tests and no practice sessions. Its only behavioural self-test is the same as in 23: turn-by-turn chat through `POST /api/v2/chat/message`, with the builder's LLM playing the customer and no bank write with a made-up customer id. It copies the export to `submission/<stem>-z.workflow.json` and writes a trace with the CLAUDE.md fields. Acceptance: `python3 planz/build.py mocks/m1/m1.md mocks/m1/openapi.json` → its trace shows chat self-tests, no `run_cases` and no practice session; then `python3 tools/rehearse.py <agent_id> --cases mocks/m1/cases.json --reps 3` → score recorded in Done; `python3 planz/build.py mocks/m2/m2.md mocks/m2/openapi.json` → export and trace present, before D-Z.
- [ ] 20 Raw-material digest and M2 spec diff (never cut). Amendment log with before/after quotes, code-list mapping, relevance labels, explicit holidays, independent fact pass and adjudication. Acceptance: `python3 builder/build.py mocks/m2/m2.md mocks/m2/openapi.json --until spec` then `python3 tools/spec_diff.py out/m2/spec.json out/m1/spec.json` → no difference except the facts the memo changes.
- [ ] 21 Our builder on M1, then D-Z. Acceptance: `python3 builder/build.py mocks/m1/m1.md mocks/m1/openapi.json` → finishes in ≤20 min; the trace's `spec` event lists the payday rule as a flow residual; then `python3 tools/rehearse.py <agent_id> --cases mocks/m1/cases.json --reps 3` → score recorded, with the payday case reported separately and a `free_<stage>` route visible in its call. D-Z at Sun 13:00: the higher M1 score wins (tie → ours); the winner's run command goes in Decisions.
- [ ] 22 Styles (never cut). Run every customer style on both agents: the A-only agent on A cases, the A+B agent on B cases. Acceptance: for each `<s>` in pieces, corrects, talkative, asks_back, in_words: `python3 tools/run_cases.py <hub-a agent> --brief A --style <s>` and `python3 tools/run_cases.py <hub-ab agent> --brief B --style <s>` → ≥12/13 combined per style.
- [ ] 23 Build-time self-tests inside build.py: only those that work on a hidden brief (no practice cases, no own customers). Dry run, save, read-back and diff; graph and lint checks; stub replay of LLM-written scenarios through the generated code; stage timing. First class: the **chat self-test**. An LLM plays the customer from scenarios written from the brief, one turn at a time through `POST /api/v2/chat/message`. Each turn is checked against the spec: the brief's identity questions, no disclosure before verification, the right stage, the closing lines. It never reaches a bank write with a made-up customer id: a scenario stops before the first write. Failures go to the trace and into the build's repair step. Acceptance: `python3 builder/build.py mocks/m1/m1.md mocks/m1/openapi.json --slot m1` → all self_test events green, with at least one chat scenario per M1 stage family, the whole build ≤30 min; `python3 builder/build.py mocks/m1/m1.md mocks/m1/openapi.json --spec tests/fixtures/spec-m1-seeded.json --slot scratch` → the chat self-test flags the seeded early disclosure and the replay self-test flags the seeded deadline n−1.
- [ ] 24 Mock M3 (never cut). `mocks/m3/`: offline API-shape mutation with renamed request and response fields, `postcode`/`birth_year` questions, cents amounts, no `title`, and renamed outcome/reason codes. Acceptance: `python3 builder/build.py mocks/m3/m3.md mocks/m3/openapi.json --slot m3` → stub conformance and local replay green; the chat self-test shows the agent asking the new questions.
- [ ] 25 Hardening. LLM timeouts and retries, best-so-far output, 25-min watchdog, idempotent reruns, trace completeness check. Acceptance: `python3 -m pytest tests/unit/test_faults.py -q` → the fault-injection tests pass.
- [ ] 26 Freeze. In a fresh clone (with `.freya-agents.json` copied in, so no new workflows are created), run the D-Z winner's exact form command on M1 and M2 with timing; write `docs/runbook.md`; tag `freeze-1`. Acceptance: `<winner run_command> mocks/m1/m1.md mocks/m1/openapi.json` and `<winner run_command> mocks/m2/m2.md mocks/m2/openapi.json` → each finishes in <30 min with export and trace present; the user approves the runbook.
- [ ] 27 Hidden day. Run the D-Z winner on both hidden briefs; bug fixes only, nothing from a hidden brief in code or prompts; commit exports and traces; send the form. Acceptance: `git log -1` shows the submission commit with both exports and traces, and the form is sent by Sun 19:45 UTC.

Cut line. Dropped in this order, only if late:

- [ ] 28 Metamorphic spec tests. Acceptance: `python3 -m pytest tests/unit/test_metamorphic.py -q` → each of 5 single-fact brief mutations changes only the expected spec path.
- [ ] 29 S4 mini-hub simulation (superseded by the walking skeleton). Acceptance: `python3 tools/run_cases.py <agent_id> --only PUB-01,PUB-02` → 2/2 with a median slowest turn ≤8 s.
- [ ] 30 Differential oracle (an LLM computes dates, amounts and outcomes from the raw text; compared with the engine). The stub replay stays. Acceptance: `python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json --until compile` → oracle check green; with `--spec tests/fixtures/spec-a-seeded.json` → flagged.
- [ ] 32 M2 live rehearsal. May be cut only if 20's spec diff is clean; mandatory if it is not. Acceptance: `python3 tools/expected_from_brief.py mocks/m2/` (ledgers reflect the memo, user-reviewed), `python3 builder/build.py mocks/m2/m2.md mocks/m2/openapi.json`, then `python3 tools/rehearse.py <agent_id> --cases mocks/m2/cases.json --reps 3` → ≥ M1's score.

Never cut: M1 (16), M2's spec diff (20), M3 (24), the styles run (22).

## Open questions

- 2026-10-10: Rule A15 with the asks_back style: literal or lenient counting of purpose questions? Decide by test in 22.
- 2026-10-10: Is `pre_process_code` honoured on a lifecycle tool? Spike S2 answers it; fallback is two engine-run close nodes.
- 2026-10-10: Outcome when the customer hangs up mid-call: the briefs are silent; use the spec's per-stage hang-up situation (neutral, logged).

## Decisions

- 2026-10-10: Architecture is a hybrid compiler with a hub-and-spoke brain, behind gate G1 (Sat 15:30) and kill switch K1 (Sat 21:00, judged on the hub runtime; a fixture spec is allowed). Fallback ladder L1–L3 shares `policy_lib`.
- 2026-10-10: Plan Z is the kit's agentic builder, kept independent of `builder/`. Its run command is `python3 planz/build.py {brief_path} {tool_api_path}`. D-Z at Sun 13:00 compares both builders on M1 live `--reps 3`, with Plan Z blind (clean working copy, no PUB tests, chat self-tests only), because Plan Z tunes itself on PUB during its build. The form has one `run_command`, so one builder serves both hidden briefs.
- 2026-10-10: Mock expected ledgers come from a separate LLM pass over the mock brief text, never from our engine, and the user reviews them.
- 2026-10-10: Builder LLM: one adapter, Anthropic API key first, Claude Agent SDK login as fallback; default model `claude-opus-5-5`.
- 2026-10-10: tts is the default delivery. llm twins speak only for questions nobody modelled; free mode runs only for flow rules nobody modelled, with code gates on every write.
- 2026-10-10: Customer dates are never extracted as type `date`/`datetime` (they anchor to the real clock); code resolves date parts against `now`. This is a deliberate exception to the CLAUDE.md tip.
- 2026-10-10: All bank writes are engine-run api nodes. `close_call` is a lifecycle tool with a code-computed outcome, a sentinel reason and a pre-hook.
- 2026-10-10: No engine defaults for brief-level values; `not_specified` is explicit, with neutral behaviour fixed in docs/design.md §2.5. A/B literal lint over `builder/`; tests live in the top-level `tests/`; few-shot examples from a synthetic non-bank domain.
- 2026-10-10: Test split: the A-only agent (`hub-a`) on A cases; the A+B agent (`hub-ab`) on B cases plus `tests/cases/a-under-b.json`. Styles run the same split.
- 2026-10-10: Sleep block Sat 21:00–Sun 03:00 UTC; the overnight queue (10) runs during it.
- 2026-10-10: The mock enforces the outcome table: a close_call reason outside its list gets `ok:false` and the ledger outcome stays empty (01: PUB-08, PUB-10). Live mocks keep the mock's codes; renamed outcome/reason codes are tested offline in M3. A failed write returns `ok:false`, so the brain retries on `ok:false`.
- 2026-10-10 (organisers): The practice bank opens only PUB-01..PUB-13; a builder cannot define its own customers. Live rehearsals reuse PUB customer ids (15, 16, 21, 32); M3 stays offline.
- 2026-10-10 (organisers): Hidden briefs come only with their own tool API file, no practice cases. On a hidden brief the only behavioural self-test is turn-by-turn chat through `POST /api/v2/chat/message` with an LLM playing the customer, never reaching a bank write with a made-up customer id: first class in 23, and the same for Plan Z in 19.
- 2026-10-10 (organisers): The workflow limit is 200 per workspace. One fixed slot per agent we want to keep (`scratch`, `brief-a`, `brief-b`, `hub-a`, `hub-ab`, `m1`, `m1-z`, `m2`, `m2-z`, `m3`, one per hidden brief and builder); no slot sharing needed.
- 2026-10-10 (organisers): Each hidden brief is one Markdown file next to its tool API file; the raw-material brief is one file with mail / policy extract / memo / code list as sections, and `{brief_path}` is that file. Several brief files stay only for Brief B on top of A. So 05 drops directory ingest, M2 (17) is one file, and 31 (`.eml`, zip and HTML ingest) is dropped. These answers win over `docs/design.md` where it plans directory or multi-file ingest, custom customers or scarce slots.
- 2026-10-10: The slow code runs (4–10 s) and the three sandbox outages of 14:36–14:52 UTC were a platform issue, now fixed (user). Re-measured at 14:59 UTC: 1.5–2.4 s per code run.
- 2026-10-10: G1, S1 part: the hub stays. Every S1 behaviour it relies on passed (docs/spikes.md). The G1 line "a code run > 1.5 s → L2" is replaced by the 8 s turn budget it stood for: L2 only if the median code run exceeds 3 s, because then a two-run turn breaks the budget (at 2.4 s it costs about 6 s). The final G1 call comes with 04.
- 2026-10-10: From S1, for 08–09 and 23:
  - The brain's first transition is `brain_response.status equals error` → `talk_error`, which speaks a literal text. A failed code run is silent and leaves the old `route` and `say_next` in place.
  - Nothing spoken before the brain's first run uses a variable that `init` sets, because the chat route skips `init`.
  - The brain stays ≤15 KB, because its code is copied into every `function_call_start` event.
  - The chat self-test reads a reply from the call's messages when the route returns 504 (long turns, farewell turns).
  - Nothing relies on `endedReason`.

## Done

- [x] 01 Setup check. Commit e67f939. Verified: `python tools/run_cases.py d59d3574-e7fa-48ce-bd5d-9433ebd57421 --brief A --out results/01-base.json` → "0 / 10 passed" (simulation ad16b001-5a51-4cad-9b23-a5083d93d3e6, 126 s, median slowest turn 3 s) and a new line in `results/log.jsonl`. Slot `scratch` = workflow c2a4ce00-4eb6-435f-acec-926102431e6e, agent d59d3574-e7fa-48ce-bd5d-9433ebd57421. Session variables: customer_id, first_name, last_name, now, title. Workspace: 2 workflows (pre-existing base agent + scratch), no limit reported. `FREYA_BANK_TEAM_KEY` regenerated (the old one had 13 chars; the mock requires 16+).
- [x] 02 Plan Z builds (2026-10-10). Commit 3eb1cc3. A: `python examples/agent_builder.py briefs/brief-a.md` → slot `brief-a`, workflow b80b162d-921c-43f4-9831-7919b009c65d, agent 413df77e-9c57-43be-951b-42fcd0edf06f, version 2, last run 8/10 (PUB-06, PUB-10 failed; its run 1 was 0/10 during a platform model outage, and the base agent control went silent too), 1642 s, trace `traces/20261010-163904.jsonl`. A+B: `python examples/agent_builder.py briefs/brief-a.md briefs/brief-b.md` → slot `brief-b` (brief-a untouched, so no `z-a`), workflow 8e78fdec-1d48-4de7-a1b9-403a6afd3066, agent cd5d5f42-5205-4277-a490-c0bd5c21afae, version 3, last run 3/3 `--brief B`, 681 s, trace `traces/20261010-170724.jsonl`. Each run was 1 rep per case; 10 scores both agents at 3 reps. The A+B agent adds notes for single-product customers, so score it on B cases only. Workflow files, generators and exports are copied to `results/02-planz/{a,b}/`; `out/` and `submission/` are left uncommitted. For 04: a tool pre-hook failed late in a call ("did not return a parseable result") and blocked `close_call` (B run 2). For 19: the kit builder read `CLAUDE.md`, `plan.md`, `results/` and reused `out/gen_brief_a.py`.
- [x] 03 Spikes S1 + S1b (2026-10-10). Commit e829565. Verified: `python spikes/s1_loop.py` (run 4, 14:55 UTC) and `python spikes/s1_loop.py --only s1b` (14:59 UTC) → PASS for D03, D04 (no dialogue call, no cascade), D07, D08 (12 hops: 13 brain + 12 hop entries, 48 s), D12 (both slots, two turns), D14 (failure visible to routing, call survives), the custom farewell and the 60 KB loop. D34: `date` extraction anchors to the real clock. D38: the chat route skips `init`. `endedReason` on chat is `assistant-ended-call`. Code runs took 1.5–2.4 s after the platform fix, the same for a 60 KB brain, whose code is copied into every `function_call_start`. Runs 1–3 hit 4–10 s runs and three sandbox outages, which showed that a failed code run is silent and detectable via `<node>_response.status = error`. Details in `docs/spikes.md`; raw results in `spikes/results/`. Slot `scratch` holds the base agent again (version 21).
