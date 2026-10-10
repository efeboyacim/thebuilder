# Freya "Build the Builder" — builder repo

Kit version 3 is in this repo (`VERSION`). Where the kit `README.md` and `docs/hackathon/` (a copy of the event web page) disagree, the kit wins.

## Goal

Write a **builder**: one command that reads a bank brief and its tool API file and creates a **phone agent** (a Freya workflow) for late-payment calls. Only the phone agents are scored. The builder runs alone; nobody edits the agent by hand.

    python3 builder/build.py <brief_path> <tool_api_path>      # the run command on the submission form

- Brief B builds on Brief A, so the builder must also accept several brief files. Tell the tool API file apart by its content (JSON with an `openapi` key), not by its position.
- One hidden brief is raw bank material (a mail, a policy extract, a memo that changes it, a code list). It may arrive as one file or several. A later memo overrides the text it amends.
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
- Update workflows in place with `freya.save_workflow(slot, ...)`. Use a few fixed slots. (Event page: 10 workflows per workspace. Kit README: 200. Treat them as scarce.)
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
  - Declare dates as `type: "date"`.
  - A custom `extraction_prompt` replaces the whole template.
- **`end_call` nodes**: the farewell comes from `terminal`. `endedReason` is the terminal's `function.name`.
- **`close_call` in `examples/base-agent.json`** is a workflow-level lifecycle tool (`alwaysRunsAt: ["end_of_call"]`). One model call fills `outcome` and `reason` from the conversation after the call. Either way it must be the last tool call and happen exactly once.
- **Product ids come from `get_account`** (`p1`, `p2`, ...). Never let the model invent them.
- **Saving and publishing**: an update must be published before a simulation uses it. `freya.save_workflow` publishes; a plain `PUT` does not. Check `agentVersionNumber`.
- **Debugging**:
  - `freya.get_call(call_id)["messages"]` is the full transcript with tool calls.
  - `route_evaluated` events show why a transition did or did not fire.
  - `POST /api/v2/chat/message` tests one turn in seconds. It may skip a first code node.

## Self-testing on a brief nobody has seen (open question)

`run_cases.py` creates mock-bank sessions by case id (`POST /v1/practice/sessions {"case": id}`), so the mock seems to know only the PUB-xx customers. It is not confirmed whether it accepts custom customers. Until it is, the builder cannot get ledger-scored tests on a hidden brief. Cheaper checks that do work:

- the workflow dry run (`freya.validate_workflow`);
- graph checks: every path reaches an end, every passthrough node ends with `always`;
- running the generated code-node Python locally on synthetic `vars`;
- chat-API smoke turns.

Ask the organisers before designing around custom cases.

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
- An item is finished only when its acceptance check ran and passed. Then report what changed, the check and its result, and stop. The user moves the item to `## Done`, or tells you to.
- If an item turns out bigger than planned or wrong, say so and propose a change to `plan.md`. Do not change the scope silently. A new finding becomes a new backlog item.
- Commit after each finished item, with a message that starts with the item number, unless the user says otherwise.

## Deadlines

Times are UTC; Istanbul is UTC+3.

| When | What |
|---|---|
| Sun 17:00 UTC (20:00 Istanbul) | Hidden briefs open. From then on only bug fixes, and nothing from a hidden brief in code or prompts |
| Sun 20:00 UTC (23:00 Istanbul) | Submission closes |

Before Sun 17:00 UTC the builder must be complete and rehearsed on self-made mock hidden briefs:
- a mutated Brief A with other numbers, identity questions, calendar and outcome codes, plus its own tool API file;
- a raw-material version: mail + policy extract + amending memo + code list.

## Status

- Kit v3 added. `env.sh` is set up but not verified yet. No builder code yet.
- Next: write `plan.md`.
- Next decision is the architecture. Candidates:
  - **Agentic**, like the example: an LLM writes the workflow JSON, tests and repairs it. Flexible, but the output varies per run.
  - **Compiler**: an LLM extracts a structured spec, and a deterministic template emits the workflow with code nodes. Reliable, but the schema can miss a rule nobody expected.
  - **Hybrid** of the two.
