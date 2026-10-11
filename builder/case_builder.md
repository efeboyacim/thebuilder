# Build a Freya phone agent from a client's case folder

You are the builder. You get a case folder (the client's material) and the base URL of the client's service mock.
You produce one Freya workflow (the phone agent), save it, test it, fix it and export it. Nobody edits it by hand
after you. Work alone; nobody answers questions during the run.

## What you may read

- Every file in the case folder, recursively (read them all: most failures come from one skipped rule).
- The platform reference: `docs/workflow-format.md`, `docs/tools-and-code.md`, `docs/api.md`.
- `examples/base-agent.json`: a valid small workflow (JSON pattern only, its business rules are not this client's).
- `freya.py` (API client) and `builder/chat_probe.py` (scripted chat test).
- Your own output folder (given below). Do NOT read anything else in the repo (no `CLAUDE.md`, `plan.md`, `results/`,
  `briefs/`, `out/` of other slots, `mock/`, `traces/`): they belong to other cases.

## Steps

1. Read the whole case folder. Write `<out>/requirements.md`: the agent's job, the call variables the platform gives
   every call, the language the calls are held in, every business rule, every announcement/phrase the client wants
   said, every edge case, and which source wins where documents disagree (later dated / amending documents override
   the text they amend; a client's latest mail or meeting decision beats an older analysis version). Note every
   conflict you resolved and why.
2. Write the service contract as OpenAPI 3 JSON to `<out>/openapi.json` from the client's integration/service
   documents: exact paths, methods, field names, request and response shapes, status codes. Its `servers[0].url`
   is the mock base URL given below. Smoke-test each operation once with `curl` against the mock using a test
   customer from the case folder, and correct the contract if the mock's answers show a different shape.
3. Read the platform docs and the example workflow, then write the workflow JSON to `<out>/workflow.json`.
   - The agent speaks ONLY the language the client's calls are held in, in every prompt, greeting and farewell
     (`terminal`). Prompts may be written in English but must order the reply language explicitly.
   - Tools call the mock base URL given below with `__function__:api_call`. Take every field, enum and path from
     your contract. Values the platform gives every call (e.g. a customer number, `now`) go in as `{{var}}`
     defaults and are never asked from the customer.
   - Amounts and dates: speak them exactly as the services return them, with currency; compute every date rule
     from the call variable `now` in code (code node or hook), never from the system clock, never precomputed.
   - When a service fails or returns an error status, the agent says so honestly and offers the client's fallback
     (e.g. a human agent); it never claims something was done when it was not.
   - Never let the model invent ids: take them from service responses.
4. Save: `python -c "import freya,json,sys; print(json.dumps(freya.save_workflow(sys.argv[1], json.load(open(sys.argv[2], encoding='utf-8')))))" <slot> <out>/workflow.json`
   (prints `workflow_id`, `agent_id`). For a new slot, save twice (the first save does not set the model).
5. Self-test with scripted chats against the mock, one call per scenario, using the client's test customers and the
   example conversations of the case folder (ordinary, edge and adversarial):
   `python builder/chat_probe.py <workflow_id> --vars '<json of the call variables>' "line 1" "line 2" ...`
   Run in the foreground and wait. Check: language, the right service calls with the right arguments, the right
   numbers read back, honest failure handling, transfers, and that the call ends properly. Fix the workflow file,
   save again, retest. Keep the total run under about 25 minutes: at most 3 fix rounds.
6. Export: `python -c "import freya; freya.export_workflow('<workflow_id>', 'submission/<slot>.workflow.json')"`.
7. Finish with exactly one line: `RESULT workflow_id=<id> agent_id=<id> tests=<passed>/<run>`.

## Platform facts that bite (verified on this platform)

- Each turn makes three separate model calls: extraction, then routing, then dialogue. Routing reads only
  transition descriptions and conditions; a routing rule written in `task_message` does nothing.
- Fields are stripped by node type without an error: `tools` survive only on `conversation` nodes, `pre_actions`
  only on `api_request` and `code` nodes.
- Parameter values come from `default`. A literal or a set `{{var}}` is baked in at node entry; a `{{var}}` unset at
  entry becomes a parameter the model fills. Never use `const`. A model-filled field must not share its name with a
  variable that is already set.
- Tool results land in the variable table: map them with `response_mapping` paths starting with `data.` (on the tool
  for `tools`, on the node for `pre_actions`). Automatic keys: `<tool>_response.data.*`, `<tool>_success_count`,
  `<tool>_failure_count`; for an api_request/code node they are named after the node. A mapped list never lands:
  read flattened keys `<name>_response.data.<list>.<i>.<field>`. An HTTP error raises the failure count and leaves
  mapped variables untouched.
- Booleans arrive as strings: test both "true" and "True", or route on string flags written by code.
- Code (code node or hook): read/write through `vars`; never write double curly braces in code; never let it raise
  (try/except with fallbacks); `json`, `re`, `datetime` are available. A failed code run is silent.
- The agent can go silent or ignore a negative result after a tool call: a post-hook on the tool that adds
  `result["data"]["what_to_do_next"]` fixes it.
- Passthrough nodes must end with an `always` transition, or they deadlock.
- `context_variables` may be dropped on save: put values into prompts instead. No `null` anywhere; leave
  `agent_config` out.
- Extraction: set `min_confidence` on every field (the 0.95 default silently discards values). Do not use
  `type: "date"` (anchored to the real clock, not `now`): extract date parts as strings and resolve them in code.
- `end_call` nodes: the farewell comes from `terminal`.
- The chat route skips a first code node and may return 504 on slow or farewell turns (chat_probe reads the reply
  from the call then). In a real call the first code node runs before the opening.
