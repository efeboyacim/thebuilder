# Freya "Build the Builder" — builder repo

Kit version 3 is in this repo (`VERSION`). Where the kit `README.md` and `docs/hackathon/` (a copy of the event web page) disagree, the kit wins.

## Goal

Write a **builder**: one command that reads a bank brief and its tool API file and creates a **phone agent** (a Freya workflow) for late-payment calls. Only the phone agents are scored. The builder runs alone; nobody edits the agent by hand.

    python3 builder/build.py <brief_path> <tool_api_path>      # the run command on the submission form

- Each hidden brief is ONE Markdown file next to its own tool API file; `{brief_path}` is that one file (organisers, 2026-10-10).
- Brief B builds on Brief A, so the builder also accepts several brief files; that is the only multi-file case. Tell the tool API file apart by its content (JSON with an `openapi` key), not by its position.
- One hidden brief is raw bank material: one file with sections (a mail, a policy extract, a memo that changes it, a code list). A later memo overrides the text it amends.
- At the end of a run: `freya.export_workflow(workflow_id, "submission/<slot>.workflow.json")` and a trace in `traces/`.

## Repo map

| Path | What |
|---|---|
| `briefs/brief-a.md`, `briefs/brief-b.md` | The two public briefs. Read all of A: most failures come from one skipped rule |
| `tools/openapi.json` | Tool API of the mock bank. Seven tools; the server URL is in `servers` |
| `public-test-set.json` | 13 practice cases incl. `identity` answers. Canonical copy (`docs/hackathon/` has a duplicate) |
| `docs/workflow-format.md`, `docs/tools-and-code.md`, `docs/api.md` | Freya platform reference. Read before writing workflow JSON |
| `freya.py` | API client: `save_workflow`, `export_workflow`, `get_call`, `run_simulation`. Do not change `AGENT_LLM` |
| `tools/run_cases.py`, `tools/score.py` | Practice runner and ledger scorer |
| `examples/base-agent.json` | Valid 2-node workflow, 4 tools. Passed 2/13. A JSON pattern, not an answer |
| `examples/agent_builder.py` + `.claude/skills/build-phone-agent/` | Example agentic builder (Claude Agent SDK + one skill). Kit v3 says it passed 10/10 on Brief A in one run |
| `docs/hackathon/` | Event page: overview, data, evaluation, submit, rules |

## Commands

```bash
cp env.example env.sh   # FREYA_API_KEY, FREYA_API_URL, FREYA_BANK_TEAM_KEY (16+ chars, same one every time)
source env.sh
python3 -c "import freya,json; print(freya.save_workflow('base', json.load(open('examples/base-agent.json'))))"
python3 tools/run_cases.py <agent_id> --brief A [--reps 3] [--style pieces|corrects|talkative|asks_back|in_words] [--only PUB-04,PUB-07] [--out results/run.json]
```

Run tests in the foreground and wait: one simulation at a time per workspace; a second one queues.

## How it is scored

A case passes only when all three hold, in **all 3 repetitions** (no partial credit):

1. **Ledger** (`tools/score.py`): `(outcome, reason)` exact; promises as (product_id, amount to 2 decimals, date); notes as (product_id, reason); callbacks as (date, time); transfer queues; the last tool call is `close_call`; no `get_account` / `create_promise` before identity is verified; on a `not_reached` case no `verify_identity` or `get_account` at all.
2. **Policy** and 3. **Honesty**: a judge model reads the transcript against the case's `checks`.

Tie-breaks: total promised amount on passed cases, then shorter build time, then earlier submission.

## Hard constraints

- **Nothing brief-specific in code or prompts.** Holidays, bands, deadlines, persuasion counts, floor fields, outcome/reason tables, queues, identity questions, field names, enum values, server URL: read them from the brief and the tool API file at build time. Writing anything from a hidden brief into the builder is disqualifying. Hardcoding Brief A/B values just breaks on hidden briefs.
- The phone agent calls only the tool API from the given file. Code nodes and hooks inside the workflow are allowed and expected.
- Do not change the Freya model (`AGENT_LLM` in `freya.py`).
- Update workflows in place with `freya.save_workflow(slot, ...)`, one fixed slot per purpose. Workflow limit: 200 (organisers).
- One builder run ≤ 30 minutes per brief, **including** its own tests. A 10-case run takes 3 to 11 minutes.
- Trace per run: start and end time, every model call (model + one-line purpose), workflow id, agent id, number of nodes and tools, every self-test and its result. No API keys in traces.
- Do not probe or load-test the platform.

## What changes in the hidden briefs

- Identity questions. `run_cases.py` already knows `postcode`, `birth_year`, `company_number`, `founded_year` besides the two of Brief A. Take the questions from the brief and the `field` enum from the tool API file.
- Numbers, limits, bands, deadlines, callback window, calendar, outcome table, note reasons, queues. Tool names stay; fields and enum values can change.
- Calls run on **several calendar days**: every date rule must be computed from `now` at call time (in code), never precomputed for 14 October.
- Up to three products per customer (Brief B rules).
- Customers talk like people: numbers in two parts, corrections, small talk, questions back, amounts and dates in words. Practise with `--style`.

## Platform facts that bite

Condensed from `docs/`. Read the full pages before relying on any of these.

- **Each turn makes three separate model calls**: extraction, then routing, then dialogue. Routing reads only transition descriptions and conditions; a routing rule in `task_message` does nothing.
- **Fields are stripped by node type without an error.** `tools` survive only on `conversation` nodes, `pre_actions` only on `api_request` and `code` nodes.
- **Parameter values come from `default`.** A literal or a set `{{var}}` is baked in at node entry. A `{{var}}` that is unset at entry becomes a parameter the model fills. Never use `const`. A model-filled field must not share its name with a variable that is already set.
- **Tool results land in the variable table.**
  - Map them with `response_mapping` paths that start with `data.`. The mapping goes on the tool for `tools` and on the node for `pre_actions`.
  - The engine also writes automatic keys: `<tool>_response.data.*`, `<tool>_args.*`, `<tool>_success_count` and `<tool>_failure_count`.
  - For a code node these keys are named after the node.
- **Booleans arrive as strings.** Test both `"true"` and `"True"`, or route on string flags that code writes.
- **Code** (`code` node or hook):
  - Read and write through `vars`; `vars.get("now")` works.
  - Never write `{{` or `}}` in code, and never let it raise: wrap it in try/except and write fallback values.
  - `json`, `re` and `datetime` are available.
  - Do arithmetic, dates and counting in code. Give the model the finished facts as text in the prompt via `{{var}}`.
- **The agent can go silent or ignore a negative result after a tool call.**
  - Fix it with a post-hook on the `api_call` tool that adds `result["data"]["what_to_do_next"]`. Here `result` is the envelope; drop `result["header"]`.
  - Seen 10 October: post-hooks can also read and write call variables.
- **Passthrough nodes** must end with an `always` transition, or they deadlock.
- **`context_variables` may be dropped on save.** Put values into prompts instead.
- **No `null` anywhere, and leave `agent_config` out.**
- **Extraction**:
  - Set `min_confidence` on every field. The 0.95 default silently discards values.
  - Do not extract the customer's dates as `type: "date"` in this event. The date types anchor relative dates
    ("Friday", "tomorrow") to the platform's own clock, and the system dates are the real clock, while every date
    rule here uses the call variable `now`. Extract date parts as strings and resolve them in code against `now`.
  - A custom `extraction_prompt` replaces the whole template.
- **`end_call` nodes**: the farewell comes from `terminal`. `endedReason` is the terminal's `function.name`.
- **`close_call` in `examples/base-agent.json`** is a workflow-level lifecycle tool (`alwaysRunsAt: ["end_of_call"]`). One model call fills `outcome` and `reason` from the conversation after the call. Either way it must be the last tool call and happen exactly once.
- **Product ids come from `get_account`** (`p1`, `p2`, ...). Never let the model invent them.
- **Saving and publishing**: an update must be published before a simulation uses it. `freya.save_workflow` publishes; a plain `PUT` does not. Check `agentVersionNumber`.
- **Debugging**:
  - `freya.get_call(call_id)["messages"]` is the full transcript with tool calls.
  - `route_evaluated` events show why a transition did or did not fire.
  - `POST /api/v2/chat/message` tests one turn in seconds. It may skip a first code node.

## Organisers' answers (2026-10-10) and self-testing

a. The practice bank opens only PUB-01..PUB-13; a builder cannot define its own customers.
b. Hidden briefs come only with their own tool API file; no practice cases.
c. The workflow limit is 200 per workspace.
d. Each hidden brief is ONE Markdown file next to its tool API file. The raw-material brief is also one file, with mail / policy extract / memo / code list as sections. `{brief_path}` is that one file.

So the builder gets no ledger-scored tests on a hidden brief. The self-tests that still work there:

- the workflow dry run (`freya.validate_workflow`), save, read-back and diff against what was sent;
- graph and lint checks: every path reaches an end, every passthrough node ends with `always`, no A/B literals;
- local replay of the generated code-node Python on synthetic `vars`;
- turn-by-turn chat via `POST /api/v2/chat/message` with an LLM playing the customer. It never reaches a bank write with a made-up customer id.

Before Sunday, live rehearsals still reuse PUB customer ids (`tools/run_cases.py`, later `tools/rehearse.py`).

## Lessons from the paper behind this format (τ-bench, arXiv 2609.04611)

- Builders failed mostly by reading the source material too shallowly.
- Validating against self-written tests missed deployment failures.
- Silent API failures went untested.
- Teams shipped the first design that worked instead of comparing architectures.
- Never weaken a failing check to make the agent pass.

## Dev machine

- The machine is Windows. Kit commands assume bash: use Git Bash. If `python3` is missing locally use `python`, but keep `python3` in the run command for the organisers.
- Open text files with `encoding="utf-8"`. The hidden raw-material brief may not be plain ASCII.
- Never commit `env.sh`, `.env`, `.freya-agents.json` or `.personas.json` (see `.gitignore`). The organisers rerun the builder in their own workspace, where our workflow ids do not exist.

## Working method: `plan.md`

`plan.md` at the repo root is the single plan. The user drives the work one item at a time.

- If `plan.md` does not exist, the first job is to write it. Write no builder code before the user has approved it.
- Structure of `plan.md`:
  1. `## Goal and architecture`: a short summary of the chosen design and why. Details go to `docs/design.md`, linked from here.
  2. `## Timeline`: milestones against the deadlines below.
  3. `## Backlog`: numbered items in working order. Each item is small enough to build and verify in one sitting (about 1 to 2 hours) and has an acceptance check:
     `- [ ] 07 Title. What it does. Acceptance: <command> → <expected result>`
  4. `## Open questions` and `## Decisions`: one dated line each.
  5. `## Done`: finished items, each with its commit hash and how it was verified.
- Work on exactly one item at a time: the one the user names. Do not start the next one unasked.
- An item is finished only when its acceptance check ran and passed. Then, without being asked:
  1. tick it and move it to `## Done`, with the date, the acceptance result and anything later items need (ids, slots, scores);
  2. commit (message starts with the item number) and add the commit hash to its Done line;
  3. report what changed and the check result in a few lines, and stop.
  If the check did not pass, the item stays in the backlog: report why and stop.
- Everything the next session needs must be in files (`plan.md`, `docs/`), because the user runs `/clear` between items.
- If an item turns out bigger than planned or wrong, say so and propose a change to `plan.md`. Do not change the scope silently. A new finding becomes a new backlog item.

## Deadlines

Times are UTC; Istanbul is UTC+3.

| When | What |
|---|---|
| Sun 17:00 UTC (20:00 Istanbul) | Hidden briefs open. From then on only bug fixes, and nothing from a hidden brief in code or prompts |
| Sun 20:00 UTC (23:00 Istanbul) | Submission closes |

Before Sun 17:00 UTC the builder must be complete and rehearsed on self-made mock hidden briefs:
- a mutated Brief A with other numbers, identity questions, calendar and outcome codes, plus its own tool API file;
- a raw-material version: one file with mail, policy extract, amending memo and code list as sections.

## Status

- `plan.md` and `docs/design.md` are approved and committed. Architecture: hybrid compiler with a hub-and-spoke "brain" (see `docs/design.md`). Work follows `plan.md`, one item at a time.
- The organisers' answers above are in `plan.md` Decisions. Where `docs/design.md` still plans directory or multi-file ingest, custom customers or scarce slots, the answers win.
- Items 01 and 02 are done (see `plan.md` Done). Next: item 03 (chat-API spikes S1 + S1b).
- Local facts:
  - `python3` on this machine is the Windows Store stub. Run `python` (3.10.11) locally; keep `python3` in the form command.
  - `env.sh` is verified. `FREYA_BANK_TEAM_KEY` was regenerated on 2026-10-10 (32 chars). Keep it from now on: practice sessions belong to it.
  - Slot `scratch` = agent `d59d3574-e7fa-48ce-bd5d-9433ebd57421`. It holds the base agent and is reused for spikes.
  - Plan Z agents from item 02: slot `brief-a` = agent `413df77e-9c57-43be-951b-42fcd0edf06f` (A), slot `brief-b` = agent `cd5d5f42-5205-4277-a490-c0bd5c21afae` (A+B). Their files are in `results/02-planz/`.
  - Run `run_cases.py` and builders in the background and wait for the completion notice. A 10-case run took 95 to 168 s.
  - The kit builder (Claude Agent SDK) run from inside a Claude Code session needs the host's `CLAUDE*`/`ANTHROPIC*` variables unset, a `python3` → `python` shim on `PATH` and `PYTHONUTF8=1`.
- Mock bank facts learned in item 01:
  - It rejects a close_call reason outside its outcome table (`ok:false`); the ledger outcome then stays empty.
  - A failed create_promise returns `ok:false` "service unavailable, nothing was recorded", not an HTTP 500.
  - The ledger (`/v1/practice/ledger/<customer_id>`) also shows `verified`, `wrong`, `hinted`, `failures` and every call with args and result: useful for debugging.
- Platform facts learned in item 02:
  - The event model can go down: around 13:45–14:00 UTC every agent, the base agent too, went silent after the first customer turn. Before debugging an all-fail run, run one case on `scratch` as a control.
  - Tool hooks can fail late in a call with "did not return a parseable result". A failing pre-hook stopped `close_call` from going out (Plan Z, Brief B run 2).
  - A newly created workflow has no event model in `agent_config` until its first update (`freya.update_workflow` sets it). The chat route needs it, so save a new slot twice.
  - The kit builder reads `CLAUDE.md`, `plan.md`, `results/` and earlier `out/` files and reuses them (its A+B build copied `out/gen_brief_a.py`, which hard-codes Brief A values).
