# Plan: Freya "Build the Builder"

## Goal and architecture

Goal: `python3 builder/build.py {brief_path} {tool_api_path}` reads a brief (one or several files: a clean spec, or
raw material such as a mail, policy extract, amending memo and code list) plus its tool API file, and creates a Freya
phone agent. It must pass hidden customers 3/3 on several calendar days, with no hand edits, in ≤30 min per brief.

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
4. **Self-tests in ≤30 min:** lint, an offline stub bank built from the openapi file, local replay through the
   generated code, Freya dry run, save, read-back, chat smoke, and `run_cases` when cases are given.
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

- [ ] 02 Plan Z builds, now. `pip install claude-agent-sdk`; run the unmodified kit builder on A, copy `out/brief-a.workflow.json` and record its agent id and version, then run it on A+B. If the A+B run reused slot `brief-a`, re-save the A copy into slot `z-a` so the overnight queue scores the right agent. Plan Z shares no code with `builder/`. It runs in parallel with 03–04 (chat API only); its own PUB tests use the simulation queue. Acceptance: `python3 examples/agent_builder.py briefs/brief-a.md` and `python3 examples/agent_builder.py briefs/brief-a.md briefs/brief-b.md` → both finish; for each, the slot, workflow id, agent id, the builder's last pass count and the trace path are recorded in Done.
- [ ] 03 Spikes S1 + S1b (chat API, throwaway `spikes/`, slot `scratch`). Covers: talk→brain→talk loop, `{{say_next}}` re-render, no cascade, 12-hop ping-pong, extraction staleness, date-type anchor, a raising code node, custom farewell; then the same with a 60 KB brain for latency, size and transcript bloat. Acceptance: `python3 spikes/s1_loop.py` → PASS/FAIL per behaviour; results in `docs/spikes.md`.
- [ ] 04 Spikes S2, S2b, S3 (PUB-01 and PUB-10 practice sessions). Covers: api bodies baked from code vars, key naming (node ≠ function), amount type, mapped product list form, failure envelope and stale keys (01 saw `ok:false` "service unavailable" on create_promise, not an HTTP 500), lifecycle close_call with sentinel reason and pre-hook, unresolved reason, close on hang-up. Acceptance: `python3 spikes/s2_api.py` → bodies printed; ledger shows exactly 1 promise, a numeric amount, reason null; G1 decision recorded in Decisions.
- [ ] 05 Builder skeleton. `builder/build.py` CLI (`--slot`, `BUILDER_SLOT`, `--until`, `--spec`), ingest of several files and directories with dedupe, classification by content, `builder/trace.py` with redaction, `builder/llm.py` adapter (API key first, Agent SDK fallback). Tests live in the top-level `tests/unit/`. Acceptance: `python3 builder/build.py briefs/brief-a.md tools/openapi.json --until ingest` → prints classified inputs and writes a trace with run_start/run_end; `python3 -m pytest tests/unit -q` → passes.
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
- [ ] 17 Mock M2 material. `mocks/m2/`: M1 rewritten as a mail, policy extract, amending memo and code list, plus its openapi copy, in one directory. Acceptance: `python3 builder/build.py mocks/m2/ --until ingest` → four brief files and one tool API classified.
- [ ] 18 Free mode and residual classification. Speech/flow classes; flow residuals become events that route to `free_<stage>` (two-miss entry only in stages with a flow residual); code gates on every proposed write. Acceptance: `python3 -m pytest tests/unit/test_free_mode.py -q` → the payday-weekday scenario records the gated promise, and a seeded invalid action (wrong product id, date past the deadline) is rejected.
- [ ] 19 Plan Z blind. `planz/build.py {brief_path} {tool_api_path}` wraps the kit builder with a blind skill copy. It accepts several files and directories, finds the openapi by content, runs no PUB tests, exports `submission/<stem>-z.workflow.json`, and writes a trace with the CLAUDE.md fields. Acceptance: `python3 planz/build.py mocks/m1/m1.md mocks/m1/openapi.json` then `python3 tools/rehearse.py <agent_id> --cases mocks/m1/cases.json --reps 3` → score recorded in Done; `python3 planz/build.py mocks/m2/` → export and trace present, before D-Z.
- [ ] 20 Raw-material digest and M2 spec diff (never cut). Amendment log with before/after quotes, code-list mapping, relevance labels, explicit holidays, independent fact pass and adjudication. Acceptance: `python3 builder/build.py mocks/m2/ --until spec` then `python3 tools/spec_diff.py out/m2/spec.json out/m1/spec.json` → no difference except the facts the memo changes.
- [ ] 21 Our builder on M1, then D-Z. Acceptance: `python3 builder/build.py mocks/m1/m1.md mocks/m1/openapi.json` → finishes in ≤20 min; the trace's `spec` event lists the payday rule as a flow residual; then `python3 tools/rehearse.py <agent_id> --cases mocks/m1/cases.json --reps 3` → score recorded, with the payday case reported separately and a `free_<stage>` route visible in its call. D-Z at Sun 13:00: the higher M1 score wins (tie → ours); the winner's run command goes in Decisions.
- [ ] 22 Styles (never cut). Run every customer style on both agents: the A-only agent on A cases, the A+B agent on B cases. Acceptance: for each `<s>` in pieces, corrects, talkative, asks_back, in_words: `python3 tools/run_cases.py <hub-a agent> --brief A --style <s>` and `python3 tools/run_cases.py <hub-ab agent> --brief B --style <s>` → ≥12/13 combined per style.
- [ ] 23 Build-time self-tests inside build.py. Stub replay of LLM-written scenarios, chat smoke, optional practice cases, stage timing. Acceptance: `python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json --slot hub-ab` → all self_test events green; `python3 builder/build.py briefs/brief-a.md tools/openapi.json --spec tests/fixtures/spec-a-seeded.json --slot scratch` → the replay self-test flags the seeded deadline n−1.
- [ ] 24 Mock M3 (never cut). `mocks/m3/`: offline API-shape mutation with renamed request and response fields, `postcode`/`birth_year` questions, cents amounts, no `title`, and renamed outcome/reason codes. Acceptance: `python3 builder/build.py mocks/m3/m3.md mocks/m3/openapi.json --slot scratch` → stub conformance and local replay green; chat smoke asks the new questions.
- [ ] 25 Hardening. LLM timeouts and retries, best-so-far output, 25-min watchdog, idempotent reruns, trace completeness check. Acceptance: `python3 -m pytest tests/unit/test_faults.py -q` → the fault-injection tests pass.
- [ ] 26 Freeze. In a fresh clone (with `.freya-agents.json` copied in, so no new workflows are created), run the D-Z winner's exact form command on M1 and M2 with timing; write `docs/runbook.md`; tag `freeze-1`. Acceptance: `<winner run_command> mocks/m1/m1.md mocks/m1/openapi.json` and `<winner run_command> mocks/m2/ mocks/m2/openapi.json` → each finishes in <30 min with export and trace present; the user approves the runbook.
- [ ] 27 Hidden day. Run the D-Z winner on both hidden briefs; bug fixes only, nothing from a hidden brief in code or prompts; commit exports and traces; send the form. Acceptance: `git log -1` shows the submission commit with both exports and traces, and the form is sent by Sun 19:45 UTC.

Cut line. Dropped in this order, only if late:

- [ ] 28 Metamorphic spec tests. Acceptance: `python3 -m pytest tests/unit/test_metamorphic.py -q` → each of 5 single-fact brief mutations changes only the expected spec path.
- [ ] 29 S4 mini-hub simulation (superseded by the walking skeleton). Acceptance: `python3 tools/run_cases.py <agent_id> --only PUB-01,PUB-02` → 2/2 with a median slowest turn ≤8 s.
- [ ] 30 Differential oracle (an LLM computes dates, amounts and outcomes from the raw text; compared with the engine). The stub replay stays. Acceptance: `python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json --until compile` → oracle check green; with `--spec tests/fixtures/spec-a-seeded.json` → flagged.
- [ ] 31 `.eml`, zip and HTML ingest (several files and directories stay). Acceptance: `python3 -m pytest tests/unit/test_ingest_raw.py -q` → MIME quoted-printable, HTML part and zip fixtures decode.
- [ ] 32 M2 live rehearsal. May be cut only if 20's spec diff is clean; mandatory if it is not. Acceptance: `python3 tools/expected_from_brief.py mocks/m2/` (ledgers reflect the memo, user-reviewed), `python3 builder/build.py mocks/m2/`, then `python3 tools/rehearse.py <agent_id> --cases mocks/m2/cases.json --reps 3` → ≥ M1's score.

Never cut: M1 (16), M2's spec diff (20), M3 (24), the styles run (22).

## Open questions

- 2026-10-10: Does the mock bank accept customers other than PUB-xx, and do hidden briefs ship practice cases? Ask the organisers; nothing in the plan waits for the answer (if yes, M3 also runs live as a bonus).
- 2026-10-10: Workflow limit: the kit README says 200, the event page says 10; the kit wins. Planned slots: `scratch`, `brief-a` (or `z-a`) and the A+B slot for Plan Z, `hub-a`, `hub-ab`, `m1`, `m1-z`, `m2`, `m2-z`, two hidden slots. If 01 shows a 10 limit, M2 and its Plan Z smoke use `BUILDER_SLOT=scratch`.
- 2026-10-10: How will a multi-file raw brief be passed to the run command? Support several paths and a directory, in both builders.
- 2026-10-10: Rule A15 with the asks_back style: literal or lenient counting of purpose questions? Decide by test in 22.
- 2026-10-10: Is `pre_process_code` honoured on a lifecycle tool? Spike S2 answers it; fallback is two engine-run close nodes.
- 2026-10-10: Outcome when the customer hangs up mid-call: the briefs are silent; use the spec's per-stage hang-up situation (neutral, logged).

## Decisions

- 2026-10-10: Architecture is a hybrid compiler with a hub-and-spoke brain, behind gate G1 (Sat 15:30) and kill switch K1 (Sat 21:00, judged on the hub runtime; a fixture spec is allowed). Fallback ladder L1–L3 shares `policy_lib`.
- 2026-10-10: Plan Z is the kit's agentic builder, kept independent of `builder/`. Its run command is `python3 planz/build.py {brief_path} {tool_api_path}`. D-Z at Sun 13:00 compares both builders on M1 live `--reps 3`, with Plan Z blind (no PUB tests), because Plan Z tunes itself on PUB during its build. The form has one `run_command`, so one builder serves both hidden briefs.
- 2026-10-10: Mock expected ledgers come from a separate LLM pass over the mock brief text, never from our engine, and the user reviews them.
- 2026-10-10: Builder LLM: one adapter, Anthropic API key first, Claude Agent SDK login as fallback; default model `claude-opus-5-5`.
- 2026-10-10: tts is the default delivery. llm twins speak only for questions nobody modelled; free mode runs only for flow rules nobody modelled, with code gates on every write.
- 2026-10-10: Customer dates are never extracted as type `date`/`datetime` (they anchor to the real clock); code resolves date parts against `now`. This is a deliberate exception to the CLAUDE.md tip.
- 2026-10-10: All bank writes are engine-run api nodes. `close_call` is a lifecycle tool with a code-computed outcome, a sentinel reason and a pre-hook.
- 2026-10-10: No engine defaults for brief-level values; `not_specified` is explicit, with neutral behaviour fixed in docs/design.md §2.5. A/B literal lint over `builder/`; tests live in the top-level `tests/`; few-shot examples from a synthetic non-bank domain.
- 2026-10-10: Test split: the A-only agent (`hub-a`) on A cases; the A+B agent (`hub-ab`) on B cases plus `tests/cases/a-under-b.json`. Styles run the same split.
- 2026-10-10: Sleep block Sat 21:00–Sun 03:00 UTC; the overnight queue (10) runs during it.
- 2026-10-10: The mock enforces the outcome table: a close_call reason outside its list gets `ok:false` and the ledger outcome stays empty (01: PUB-08, PUB-10). Live mocks keep the mock's codes; renamed outcome/reason codes are tested offline in M3. A failed write returns `ok:false`, so the brain retries on `ok:false`.

## Done

- [x] 01 Setup check. Commit e67f939. Verified: `python tools/run_cases.py d59d3574-e7fa-48ce-bd5d-9433ebd57421 --brief A --out results/01-base.json` → "0 / 10 passed" (simulation ad16b001-5a51-4cad-9b23-a5083d93d3e6, 126 s, median slowest turn 3 s) and a new line in `results/log.jsonl`. Slot `scratch` = workflow c2a4ce00-4eb6-435f-acec-926102431e6e, agent d59d3574-e7fa-48ce-bd5d-9433ebd57421. Session variables: customer_id, first_name, last_name, now, title. Workspace: 2 workflows (pre-existing base agent + scratch), no limit reported. `FREYA_BANK_TEAM_KEY` regenerated (the old one had 13 chars; the mock requires 16+).
