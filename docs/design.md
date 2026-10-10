# Design: `builder/build.py`

Status: proposed 2026-10-10. The plan and backlog are in [../plan.md](../plan.md). Four independent read-only reviews
shaped this design: rules audit, platform skeptic, alternative design, hidden-brief red team. Their findings are
folded in below.

## 0. Summary

1. **Hybrid compiler.**
   - Claude reads the brief material (a clean spec, or a mail, policy extract, memo and code list) into a validated
     JSON **spec**.
   - The tool API is parsed from the openapi file without an LLM.
   - Python code compiles spec + API into the workflow. No LLM writes workflow JSON.
2. **Generated agent: a hub-and-spoke "brain".**
   - One generic Python policy engine, parameterised by the spec, runs in a code node and makes every decision.
   - Talk nodes only extract the customer's turn and speak the brain's `say_next` by tts.
   - Every bank write is an engine-run `api_request` node. `close_call` is a lifecycle tool with an outcome computed
     in code.
   - Flow rules the schema could not model go to a **free-mode** llm stage, and code gates every write it proposes.
3. **Nothing brief-specific in the builder.**
   - A binding layer maps engine roles to the openapi request and response fields.
   - Outcome situations are open records.
   - Every behaviour switch is a required spec field with an explicit `not_specified`. There are no Brief A defaults.
   - A lint checks for A/B literals.
4. **Self-tests in ≤30 min:**
   - schema checks and lint;
   - an offline stub bank generated from the openapi file;
   - local replay of scenarios through the generated code;
   - Freya dry run, save, read-back and chat smoke;
   - `run_cases` when practice cases are supplied.
5. **Safety and rehearsal.**
   - Platform spikes gate the design (G1).
   - A walking skeleton must pass PUB-01..03 live by Sat 20:30 UTC (kill switch K1).
   - Mock hidden briefs run live on reused PUB customer ids, with our own expected ledgers and `now` dates.
   - Plan Z, the kit's agentic builder, shares no code with ours and stays ready until decision D-Z.

```text
brief files ──┐                       ┌─ LLM: digest ─▶ LLM: spec ─┬─▶ validate ─▶ repair (≤2)
openapi.json ─┼─▶ ingest+classify ─▶  │                LLM: fact pass ─┘        │
cases.json? ──┘       │               └──────────────────────────────────────── spec.json
                      └─▶ ToolAPI ─▶ stub bank                                     │
                                         │                                         ▼
                                         └──────────▶ self-tests ◀── compile ─▶ workflow.json
                                                                                   │
                                     Freya: dry run ▶ save/publish ▶ read-back ▶ chat smoke ▶ export
```

---

## 1. Builder pipeline

```bash
python3 builder/build.py <files|dirs...> [--slot S] [--until ingest|spec|compile] [--spec fixture.json]
```

The run command on the form is `python3 builder/build.py {brief_path} {tool_api_path}`. The builder also accepts
several brief files (Brief B builds on A) and directories (raw material in several files).

`--spec` skips the LLM stages and compiles a given spec. It exists for test fixtures only, so that the hub runtime can
be judged on Freya independently of the extractor.

### 1.1 Stages

| # | Stage | LLM | What it does | Est. time |
|---|---|---|---|---|
| 1 | Ingest | no | Expands directories and dedupes paths. Reads every file as utf-8 with a fallback. Classifies by content (see below). Slot: `--slot`, else env `BUILDER_SLOT`, else `hub-` + the brief stems joined by `+` (Decisions, 05). The env var lets a test redirect the slot while keeping the exact form command. `.eml`/zip/HTML decoding is below the cut line | <1 s |
| 2 | ToolAPI | no | Server URL; per tool: request fields, types, enums, required fields, descriptions, response schema. Generates the stub bank (§4.1) | <1 s |
| 3 | Digest | yes | One consolidated, numbered rule list with source quotes (detail below) | 2–4 min |
| 4 | Spec + fact pass | yes, 2 calls in parallel | (a) Fills the spec schema with structured output, from digest + ToolAPI summary. (b) An independent fact pass with a different prompt over every number, enum, date and code, read from the **raw sources** | 2–4 min |
| 5 | Adjudicate | yes, only on mismatch | Each disagreement between 4a and 4b is decided against the raw quotes | 0–2 min |
| 6 | Validate and repair | yes, ≤2 rounds | Deterministic validation (§2.4). The error list goes back to the LLM for repair | 0–3 min |
| 7 | Compile | no | Spec + ToolAPI → workflow JSON (§3) | <1 s |
| 8 | Self-tests | one LLM call writes scenario scripts (started in parallel with 4) | §4.1 | ~1 min |
| 9 | Freya | no | `validate_workflow` (dry run) → `save_workflow(slot)` (publishes) → read back and diff → chat smoke → `export_workflow(id, "submission/<slot>.workflow.json")` | 1–3 min |

Target 10–15 min. A watchdog at 25 min ships the best validated workflow so far and records why in the trace.
Artifacts go to `out/<slot>/`: `digest.md`, `spec.json`, `workflow.json`, `checks.json`. They are outputs, never
builder code.

**Classifying input files by content:**
- JSON with an `openapi` key: the tool API.
- JSON list whose items have `expected`: practice cases.
- Everything else: brief material, kept in the order given. Later files may amend earlier ones.

**Digest output** (step 3). Besides the rule list:
- an **amendment log**: target section, before quote, after quote, `effective_from`;
- a **relevance label** with a quote for every section: binding, ignore, or wish (not binding);
- holidays as **explicit dates**, also when the source names them;
- the **code-list mapping**: code → meaning → where it is used.

### 1.2 The builder's model calls

`builder/llm.py` is one adapter with two back ends:
- the Anthropic Messages API, with tool-use JSON output, when `ANTHROPIC_API_KEY` is set;
- otherwise `claude_agent_sdk.query` with no tools, which uses the Claude login (the same mechanism as the kit's
  example builder).

Default model: `claude-opus-5-5`, overridable with `BUILDER_MODEL`. Every call writes a `model_call` trace event:
model, one-line purpose, seconds, tokens, ok. Retries: 2, with backoff.

Prompts describe the schema in generic terms. Few-shot examples come from a **synthetic non-bank domain** with random
values, so the extractor is never pulled toward Brief A's numbers.

---

## 2. Spec

### 2.1 From brief to spec

- **Clean spec** (like Brief A, or A + B): the digest numbers the rules and applies "every rule of Brief A applies
  unless a rule below replaces it".
- **Raw material** (mail + policy extract + amending memo + code list): the digest also handles:
  - the memo: a later memo overrides the text it amends, and the amendment log records each change with quotes. A
    withdrawn section is dropped;
  - effective dates: kept as `effective_from` and resolved in code against `now`, because calls run on several days;
  - non-binding text ("ignore §8", "maybe offer a waiver"): labelled, not turned into rules;
  - holidays given by name: converted to explicit dates two years ahead;
  - codes marked back-office only: these go to `forbidden_codes`.
- Two independent extractions (spec and fact pass) are diffed field by field. A mismatch is adjudicated against the
  raw quote, not against the digest, so a digest misread cannot confirm itself.

### 2.2 Schema (`builder/spec_schema.json`)

Every value carries `source_refs` (digest rule ids and quotes). Brief-level switches have **no default**: each one is
either set from the brief or explicitly `not_specified`. A `not_specified` value gets the neutral behaviour in §2.5,
which was chosen without looking at Brief A.

| Section | Contents |
|---|---|
| `bindings.call_vars` | Call-variable role → variable name. Roles (`builder/bindings.py`): `customer_key`, `clock`, `given_name`, `family_name`, `honorific`, `company`; the last three optional |
| `bindings.tools`, `bindings.request` | Tool role (`verify`, `lookup`, `record_promise`, `record_note`, `book_callback`, `transfer`, `close`) → tool name. Per tool role, per body field path: source (`call_var`, `engine`, `literal`, `template`) and converter (`number_2dp`, `cents_int`, `string_2dp`, `date_iso`, `time_hhmm`, `datetime_join`). Engine roles per tool role are fixed in `TOOL_ROLES` (e.g. question, answer; product_ref, amount, date; queue; outcome, reason) |
| `bindings.response` | Per tool role, response role → body path: `ok`, `error`, `verified`, `fully_verified`, `attempts_left`, `hint`, `products`, `promise_ref`; and `item`: item role → path inside one product (`ref`, `kind`, `due_amount`, `days_overdue`). Label and floor fields are read by their API name from the raw row |
| `opening` | tts template; required content; words forbidden before verification; confirmation semantics (what counts, max asks, whether an answered question uses an ask); `unclear` situation |
| `third_party` | Callback model: `day_set` expression, `windows[]`, inclusive, granularity, `min_lead`, after-now, `re_asks`. Situations (wrong number, deceased, others) with required sentences. Disclosure ban |
| `identity` | Ordered `questions[]`, each with: openapi field value, ask text, normalizer pipeline (digit words, strip, upper, date parse with `day_first`), `complete_pattern`, accumulation across turns, `ask_when`. Limits: `total`, `per_question`, `on_wrong` ∈ {repeat, next}. Hints: value, action text, `counts_as_wrong`. Decline: explain-once text, max refusals, whether a purpose question counts as a refusal, counter scope. `authorised_parties` |
| `products` | Naming template per type; disclosure template; `bands[]` (selector expression, action ∈ negotiate/transfer/close, deadline expression, attempts); floor expression per type; `product_order` expression |
| `calendar` | Business weekdays; explicit holidays; `week_start`; weekday semantics (this/next style or nearest upcoming); `non_bd_shift` ∈ {next, prev, ask}; `earliest_date` expression; `named_periods[]` (phrase → expression or `vague`) |
| `negotiation` | `first_ask` (amount, date, template, scope); `amount_elicitation`; `forbidden_offer_terms` (terms the brief forbids offering, checked by the policy lint); `too_much_policy`; `below_floor_policy`; `attempt_consumption`; `attempts_scope`; `ladder[]` (amount expression, date expression, bands, clamp); `dedupe_refused_offers`; `vague_policy`; `after_deadline_policy`; `overflow_policy`; `confirm_mode`; read-back template and question; `post_promise_sentence` and its scope; change-after-record sentence |
| `failure_policy` | Per tool: retries, situation when exhausted (single product: close; several products: note and continue, per the brief), honesty sentence. Retry counters are kept per product in brain state |
| `events[]` | Trigger description (feeds extraction), stage (pre, post or any), `min_occurrences` (default 1; e.g. "says twice"), precedence, `suppresses`. Actions: say, ask then branch (condition expression), callback, transfer (queue), note (reason; scope current or all open), close (situation), `ask_once_first`. One brief rule can become several rows, e.g. one per stage |
| `situations[]` | Open records: id, trigger, outcome, reason, note_reason, queue, required sentence, farewell |
| `notes` | Enabled, `min_products`, timing, `on_transfer` ∈ {drop, keep}, text template |
| `closing` | `aggregation`: `promise_wins`, `transfer_overrides_promise`, `all_same_note` map (e.g. all paid → paid), `otherwise` ∈ {`first_in_order`, `priority[]`}. The set of outcomes that take no reason, taken from the table. Hang-up situation per stage |
| `phrases` | Exact sentences the brief prescribes |
| `residual_rules[]` | Rule text, stage, class ∈ {speech, flow}, trigger (for flow), how it is executed |
| `rule_index` | Every digest rule → spec paths, or a residual id. 100% coverage is required |
| `forbidden_codes` | Codes the agent must never send |

**Spec v0 (item 07).** `builder/spec_schema.json` holds the slice sections in their final generic form:
`call_variables`, `bindings`, `calendar` (business weekdays and explicit holidays only), `opening` (template,
required content, forbidden words, confirmation counting), `third_party` (callback model with a `day_rule`
expression, windows and limits; `terminations[]` such as a wrong number, each pointing to a situation), `identity`,
`disclosure` (intro, one sentence per product, a name per product kind), `negotiation.first_ask`, `confirm`
(read-back, question, recording sentences), `situations[]`, `closing` (outcome table, `transfer_overrides_promise`,
hang-up situation per stage with `stated_in_brief`), `phrases`, `residual_rules`, `rule_index`, `forbidden_codes`.
Every other rule (dates, amounts, persuasion, events, notes, several products, failures) is a residual rule in v0;
items 11, 12 and 14 add their sections and move those rules out. `refs` are digest rule ids; template placeholders
are `{{name}}`; codes are checked against the tool API's enums, or must occur verbatim in the brief. Validation
(`builder/spec.py`) checks expressions against the AST whitelist and their names; evaluating them on synthetic
inputs needs `policy_lib` and comes with item 08.

**Item 11 sections.** `calendar` adds `weekday_semantics` (calendar_week, nearest) and `named_periods[]` (phrase →
date expression). `products` holds `bands[]` (id, selector, deadline expression, attempts, action negotiate/close with
a situation), the `floor` expression and the `unmatched` situation. `negotiation` adds `non_business_day` (next, prev,
ask + template), `after_deadline` (offer_deadline, ask + template), `vague` (re-asks, template, then
propose_deadline/no_answer + template), `ask_date_template`, `ladder[]` (step, bands, amount and date expressions,
template), `non_answer_is_refusal`, `dedupe_refused_offers` and `on_exhausted`. `failure_policy` gives retries and an
exhausted situation for lookup, record_promise, book_callback and transfer. Offer templates take `amount`, `date`,
`deadline`, `named_date` and the product placeholders; ladder expressions take `floor`, `deadline` and
`said_too_much`. The engine checks every date it would agree or offer (today or later, a business day, not after the
deadline) and clamps ladder dates into that range; a refusal of any offer on the table is a flat refusal and uses
the next ladder step of the product's band. Fixture: `tests/fixtures/spec-a-v1.json`.

**Item 12 sections.** `negotiation.amounts`: `accept_range` (floor_to_due, due_only), `too_much` and `below_floor`
(offer_floor or refusal + template; `named_amount` is the customer's amount), `floor_offers` (how often those two may
offer the floor) and `same_amount_template` (a ladder step that keeps the floor already offered and only moves the
date, so the floor is not named twice). `events[]`: id, trigger (extraction label), stages, `min_occurrences`,
`suppresses` (event ids and `persuasion`), `below_min_template` (said while it occurred fewer times), `template`,
`on_yes`/`on_no` (a yes/no question), `then`, `then_before_verification`; targets are a situation, `continue` or
`not_specified`. List order is precedence (the extraction enum keeps it). `side_questions`: `answers[]` (id, trigger,
template) and `other` (answer_from_facts → a `talk_<stage>_llm` twin that answers from `{{facts}}`, empty before
verification, then says `{{say_next}}`; or no_info → the fixed sentence). New extraction fields: `x_event`, `x_reply`
(yes/no to the agent's question) in every stage that has events, `x_amount` in negotiate and confirm. A customer
amount below the full amount sets `said_too_much`. Every call has a 40-turn guard (`engine.TURN_LIMIT`).

### 2.3 Expressions

Some values are computed per call: deadlines, floors, ladder steps, band selectors, callback days, `earliest_date`.
These are small expressions in a whitelisted Python subset. They are checked with an AST walk: names, numbers,
comparisons, arithmetic, conditional expressions, and calls to helpers only.

| Area | Helpers |
|---|---|
| Dates | `add_business_days(d, n)`, `add_calendar_days(d, n)`, `roll(d, 'next'|'prev')`, `last_bd_of_week(d, offset)`, `last_bd_of_month(d)`, `due_date(product)` |
| Numbers | `min`, `max`, `ceil_to(x, step)`, `round_half_up(x, places)` (uses Decimal) |

Inputs: product roles, `today`, the time in `now`, state flags (`said_too_much`, `hardship`, …).

The builder evaluates every expression on synthetic inputs before it compiles.

### 2.4 Deterministic validation

- The spec validates against the JSON Schema.
- Every enum value used is in the openapi enum or the code list, and none is in `forbidden_codes`.
- A free-string field such as `reason` must appear verbatim in the brief or the code list.
- Every required request field of every tool used has a binding.
- Every response path exists in the response schema.
- Every `{{x}}` in a template is a declared call variable or an engine variable.
- Expressions pass the AST whitelist and evaluate on synthetic inputs.
- Every terminal path the engine can produce maps to a situation that has a valid (outcome, reason).
- Every note reason maps to a close reason.
- Every digest rule is indexed.
- Every residual rule has a class, and every flow residual has a trigger.

### 2.5 Neutral behaviour for `not_specified`

`not_specified` never means "do what Brief A does". These choices are fixed now, before any hidden brief is seen.
Every one that is used produces a trace `warning`.

| Switch | Neutral behaviour |
|---|---|
| Weekday semantics | Resolve to the nearest upcoming day. The read-back always names the full date |
| Non-business day | Ask for another day |
| Vague date | Ask for an exact date. A second vague answer counts as no answer |
| Callback window | Accept any time in the future |
| Re-ask limits | 1 |
| Hang-up situation per stage | The situation the LLM marks as closest in meaning (logged) |
| Business weekdays (item 08) | Monday to Friday |
| Confirmation asks, refusals (item 08) | 2 in total (one re-ask, then the second closes) |
| After a wrong identity answer (item 08) | Ask the same question again |
| Purpose question during identity (item 08) | Not a refusal; the question is asked again |
| Callback window ends, `after_now` (item 08) | Inclusive; the callback must be later than `now` |
| A situation reference that is `not_specified` (item 08) | The stage's hang-up situation |
| Date after the deadline (item 11) | Ask for another day |
| Band attempts (item 11) | 1 |
| Band deadline (item 11) | No upper limit; a vague answer is then a non-answer |
| Floor (item 11) | The full amount due |
| Non-answer to an offer (item 11) | A flat refusal (uses an attempt) |
| Repeating a refused offer (item 11) | Not repeated: the step is skipped |
| Tool retries (item 11) | 1 retry with the same values, then the stage's hang-up situation |
| Accepted customer amounts (item 12) | From the floor up to the full amount |
| Too much, below the floor (item 12) | A flat refusal |
| Floor offers (item 12) | 1 |
| Other side questions (item 12) | The no-information sentence |
| An event target (item 12) | Continue with what the agent was asking |

---

## 3. Generated workflow

### 3.1 Graph

```text
init(code) ─always─▶ opening(conv, tts) ─always─▶ brain(code) ◀───────────────────────────────┐
                                                    │ route==X (deterministic, no LLM)        │
      ┌──────────────────┬──────────────────┬───────┴──────────┬──────────────────┐         │
      ▼                  ▼                  ▼                  ▼                  ▼         │
 talk_<stage>_tts   talk_<stage>_llm   free_<stage>       api_<tool>         end_<k>        │
   (conv, tts)        (conv, llm)      (conv, llm)        (api_request)     (end_call,      │
      │always            │always          │always          │ ok/fail ─▶ <tool>_ok|_fail(tts)│
      └──────────────────┴──────────────────┴──────────────────┴─ else ───────────────────────┘
                                                                      farewell)──▶ call ends ─▶ close_call (lifecycle)
talk_error (conv, tts): reached when brain's own run failed (brain's first transition).
talk_fallback (conv, tts): reached only by brain's last `always`. A separate node, so the two never merge.
```

Stages: `open`, `third`, `identity`, `negotiate`, `confirm`.

### 3.2 Nodes

| Node | Type | Entry | Transitions | Purpose |
|---|---|---|---|---|
| `init` | code, initial, passthrough | silent | always → opening | Writes defaults: `final_outcome`/`final_reason` (the hang-up situation of the opening stage), `farewell`, `say_fallback`, an empty `state`. Tiny, so a chat run that skips it changes nothing: brain initialises lazily |
| `opening` | conversation | tts: opening template | always → brain | Fixed first sentence and identity question. Carries the `open` extraction schema |
| `brain` | code, passthrough | silent | (1) the brain's own run failed → `talk_error`; (2) `route == X` for each target; (3) always → `talk_fallback`. Every target appears in exactly one transition | The policy engine (§3.3) |
| `talk_<stage>_tts` | conversation | tts `{{say_next}}` | always → brain | Default delivery. No model call. Exact sentences stay exact |
| `talk_<stage>_llm` | conversation | llm | always → brain | Only when the customer asked something nobody modelled (`side_q=other`). Answers in one sentence from `{{facts}}` (empty before verification) or the stage rules, then says `{{say_next}}` word for word. No tools. Before verification the prompt states the ban in general terms ("do not state the reason for the call, any product, amount or debt") and never lists the forbidden words, so the policy lint stays meaningful |
| `free_<stage>` | conversation | llm | always → brain | Free mode (§3.6). No tools |
| `talk_error` | conversation | tts `{{say_fallback}}` | always → brain | Safe re-ask after a failed brain run. Only brain's transition (1) targets it |
| `talk_fallback` | conversation | tts `{{say_fallback}}` | always → brain | Safe re-ask. Only brain's final `always` targets it. No `route == X` targets either node, which avoids the transition-merge trap |
| `api_<tool>` | api_request, passthrough | silent | When brain pre-decided both texts (binary outcomes only): mapped ok → `<tool>_ok` (tts `{{say_ok}}`) and mapped fail → `<tool>_fail` (tts `{{say_fail}}`). These are distinct nodes, so the transitions do not merge, and each carries the next stage's extraction schema. Last: always → brain | One per bank tool used. Node key = function name. Non-binary results (e.g. verify with a hint, or the last wrong answer) always go back to brain |
| `end_<k>` | end_call | silent; terminal `message_mode: custom`, `spoken_message: {{farewell}}` | — | Farewell in code-built text. `function.name` = situation id |
| `close_call` | workflow-level lifecycle tool, `alwaysRunsAt: [end_of_call]` | — | — | Exactly one close, always last |

Settings on every node type:
- Every code and api node has `on_enter: silent` and `disable_inactivity_monitor: true`.
- Talk nodes set `max_node_turns` to 200 and no `max_turn_transition`. Every limit the brief sets is counted in
  brain state.

### 3.3 The brain

- **Code.** `policy_lib` (calendar, expressions, normalizers, validators, templates) plus the engine, compiled into one
  code string. The spec is embedded compactly, with `{`/`}` written as `\x7b`/`\x7d`, so no `{{` or `}}` ever
  appears. Only `json`, `re`, `datetime` and builtins are used. Target ≤15 KB. If S1b shows size or latency hurting,
  it is split into `brain_pre` (open, third, identity) and `brain_post`.
- **Never raises.** The body is wrapped in try/except. On a caught error it writes `say_fallback` and `brain_error`
  and sets `route` to a value no transition matches, so the final `always` takes it to `talk_fallback`. A platform
  failure of the run itself (timeout, sandbox error) takes transition (1) to `talk_error`.
- **State** (one JSON string in vars):
  - stage and turn counters;
  - ask, refusal and decline counters with their scopes;
  - identity: question index, partial answers, wrong count;
  - products with band, deadline, floor and labels;
  - per product: offers[] as (amount, date, kind, refused), `floor_offered`, `attempts_used`, status, agreed terms;
  - hardship and `said_too_much` flags;
  - recorded promise ids; write retry counters per product;
  - deferred notes;
  - pending api call and its success/failure counter snapshot;
  - evidence of the last processed turn.
- **One user turn.** An invocation that follows a talk-node route is exactly one new turn. The brain:
  1. reads the extraction fields of that talk node;
  2. skips them if their `evidence` equals the last turn's;
  3. if nothing was extracted, re-asks once without counting; a second miss counts as a non-answer;
  4. runs regex fallbacks on `said` (yes / speaking / own name, four digits, month words);
  5. applies event precedence: a date or amount slot beats a refusal; `suppresses` lets hardship block persuasion
     and restructuring;
  6. runs the stage policy;
  7. writes `route`, `say_next`, the api arguments and `final_*`.
- **After an api node.** Success means a failure-counter delta of 0 and the bound `ok` role in (`true`, `True`).
  Before routing a write, the brain checks it is not a duplicate: never a second `create_promise` for a product in
  the recorded set.
- **Outcome.** `final_outcome`/`final_reason` hold the current situation all through the call, so a hang-up still
  closes correctly. The reason sentinel is `-`.

**Implementation (item 08).**
- `builder/policy_lib.py` (calendar and expression helpers, spoken numbers, dates, times, answer normalizers,
  converters, templates) and `builder/engine.py` (the state machine) are the brain. Both run locally and inside the
  code node: imports `json`/`re`/`datetime` only, no `global`, no doubled braces, no f-strings.
- `builder/brain.py` builds the code string: both modules without docstrings and comments, one space per indent
  level, the compact spec as an ASCII literal with escaped braces, and the expressions as lambdas
  (`builder/exprs.py` prints them; the brain needs no `eval`). Everything sits inside `def _brain(vars)`, so it runs
  with separate global and local namespaces. On the Brief A v0 fixture it is about 50 KB (12 KB of it the spec).
- Names shared with the compiler (`engine.py`): talk nodes `talk_<stage>`, api nodes `api_<role>`, the end node
  `end_call`; `route` value `brain_failed` after a caught error; extraction fields `x_<slot>` (`contract.py` builds
  each stage's fields from the spec); api arguments `a_<role>__<body field>`; mapped responses `r_<role>__<role>`;
  outputs `say_next`, `say_fallback`, `farewell`, `final_outcome`, `final_reason` (`-` = none), `brain_state`.
- `builder/replay.py` emulates the Freya runtime (counters, flattened and mapped keys, lifecycle close) around the
  stub bank; `tests/unit/test_engine_slice.py` drives it.
- `confirm.on_recorded` (spec schema) names the situation once a promise is recorded.

### 3.4 Extraction contract

Per talk node. `min_confidence` 0.3–0.5, `max_retries` 0–1. **No `date` or `datetime` types**: those anchor to the
real clock, not to `now`. Every description says "from the customer's LAST message only; `none` if absent".

| Field | Type | Values |
|---|---|---|
| `main` | string enum, ≤8 values | Stage-specific. `open`: confirmed, not_confirmed, someone_else, wrong_number, deceased, unclear. `third`: gives_time, no_time, offers_to_pay, wrong_number, deceased, unclear (third-party situations from the spec add values here). `identity`: answers, refuses, unclear. `negotiate`: yes_today, accepts, refuses, proposes, too_much, unclear. `confirm`: yes, no, change |
| `side_q` | string enum | robot, scam, purpose, why, other, none |
| `event` | string enum | The spec's event ids (hardship, restructuring, human, paid, dispute, no_talk, … as the brief defines them), plus `none` |
| slots | string | Identity answer as said; digit groups in spoken order; month word; date parts (relative day, weekday, this/next, day of month, month); time; amount; scope (all, current, listed); per-product references p1..pN |
| `said` | string | The message verbatim |
| `evidence` | string | The span that justifies `main` and `event` |

Code does all parsing: word numbers, two-part numbers (merged by the completeness check), corrections (the last value
wins), and dates resolved against `now` with the spec's weekday semantics.

### 3.5 Api nodes, bindings, writes

- Body fields come from `bindings.tool_request` and are baked at node entry from vars the brain set. `customer_id`,
  or whatever the binding names it, comes from the call variable.
- Settings: `maxRetries: 0` (retries follow `failure_policy`, run by the brain); `timeoutMs: 10000`.
- Node-level `response_mapping` with `data.` paths for every response role.
- For binary results, the brain pre-composes `say_ok`/`say_fail` before routing to the api node. The node then routes
  on its mapped ok straight to `<tool>_ok` or `<tool>_fail` (tts), so a common write turn costs one brain run. Any
  other result goes back to the brain.
- Promises are recorded after the single confirmation, as a linear chain over the agreed products. The first link
  says "One moment, please." and claims nothing.
- Notes are flushed in the turn the brain decides to close, never after a transfer when `notes.on_transfer` is `drop`.
- Closing: `close_call` is a lifecycle tool with baked `{{final_outcome}}`/`{{final_reason}}`. Its `pre_process_code`
  removes `reason` when it equals the sentinel or the outcome is in the reason-omission set. **Fallback** if spike S2
  shows the hook does not run on lifecycle tools: two engine-run close nodes (with and without a reason) entered just
  before the end node.

### 3.6 Free mode: flow rules nobody modelled

A raw brief is the likely place for a rule the schema cannot express. Such a rule must still take effect, and must not
silently disappear into a prompt.

1. The spec step classifies every residual rule as **speech** (wording; it goes into phrases and llm-twin prompts) or
   **flow** (affects a tool, outcome, amount, date or count).
2. The repair loop first tries to express a flow rule through the generic extension points: an expression, an event
   row with the open action set, a validator constraint.
3. A flow rule that is still residual becomes an **event**: an extraction id plus trigger text from the rule, bound to
   a stage.
4. The brain routes to `free_<stage>` when that event is extracted. Two unparsed turns in a row also enter free mode,
   but **only in a stage that has a flow residual**. Everywhere else the §3.3 rule applies: re-ask once uncounted,
   then the second miss counts as a non-answer. One trigger never has two meanings.
5. `free_<stage>` is an llm conversation node with **no model-called tools**. Its prompt holds the stage's digest
   rules (the residual one verbatim), the facts and the agreed state. Its extraction captures the proposed action:
   `promise(product, amount, date)`, `note(reason)`, `transfer(queue)`, `callback(date, time)`,
   `close(situation)`, `continue` or `done`.
6. The brain executes an action only after **every code gate** passes:
   - identity verified;
   - enums ⊂ openapi;
   - product id from `get_account`;
   - the spec's date and amount validators;
   - one promise per product;
   - a confirmed read-back before `create_promise`;
   - no notes on a transfer.
7. A rejected action gets a `say_next` explanation and the call stays in free mode. `done` returns to the normal flow.
8. The trace lists every flow residual, its trigger and how it is executed. M1 contains one such rule (backlog 19).

### 3.7 What runs where

| Mechanism | What it carries |
|---|---|
| **Code** (brain) | Every count, date, band, deadline, floor, ladder step, validation, identity step, precedence, situation, note, closing aggregation, and every tool argument |
| **Deterministic routes** | All routing. There is no `matches_intent` anywhere |
| **Extraction** | Classifying the customer's latest turn and capturing slots |
| **tts** | Every sentence the policy prescribes: opening, asks, offers, read-back, sympathy, condolence, the text-message sentence, farewell |
| **Prompt** | Only the llm twins (answers to unmodelled questions, residual speech rules, tone) and free mode |

### 3.8 Latency budget

Target: slowest turn ≤8 s. `run_cases` warns above a 10 s median.

| Turn | Model calls | Code runs | HTTP | Estimate |
|---|---|---|---|---|
| Plain reply or re-ask | 1 (extraction) | 1 | 0 | 2–5 s |
| Identity answer | 1 | 1–2 | 1 | 3–7 s |
| Last identity answer, then disclosure | 1 | 2 | 2 | 4–9 s |
| Brief B confirmation, 3 promises | 1 | 2 | 3 | 5–10 s |

S1 and S1b measure the cost of one code run, which is the unknown that decides these numbers.

---

## 4. Validation and testing

### 4.1 At build time (inside the 30 minutes)

| Check | What it catches |
|---|---|
| Graph lint | Unreachable node; a node without a path to an end; passthrough without a final `always`; two transitions with the same target (merge trap); code/api node not silent; `tools` on a non-conversation node; `pre_actions` on a conversation node; `null`; `const` |
| Code lint | `{{` or `}}` in any code; a `{{var}}` used before any node sets it |
| Policy lint | (a) A forbidden pre-verification word in any text reachable before identity is verified (tts, system prompt, llm-twin prompts; those prompts state the ban without listing the words). (b) Any `negotiation.forbidden_offer_terms` term in any template |
| Leak lint (dev, `tools/leak_lint.py`) | A/B literals in `builder/`. The candidate literals are extracted from `briefs/*.md` at lint time, so the lint itself holds no list. Tests live in the top-level `tests/` and are not scanned |
| Stub-bank conformance | Every api body rendered from synthetic brain state is validated against the request schema (required fields, enums, types, formats). Schema-shaped responses, including hint, wrong-answer, `ok:false` and 500 paths, are fed back through the brain |
| Local replay | Scenario scripts (act sequences + expected ledger), written by an LLM from the raw sources, run through the **generated code string** with the stub bank |
| Freya dry run | `validate_workflow` |
| Save, publish, read-back diff | Fields the platform stripped silently |
| Chat smoke | Opening line: required content, no forbidden words. A third-party turn. No write tool is ever called with a fake id |
| `run_cases` | Only when practice cases are among the inputs |
| *(cut line)* differential oracle | Deadlines, attempt dates, callback validity and outcomes computed by an LLM from the raw text for generated `now` values (Fridays, weekends, holiday weeks, after hours), compared with the engine |

**Limit we design around.** The mock bank creates practice sessions by PUB case id. Without custom customers, the
builder cannot run ledger-scored live tests on a hidden brief. The design does not wait for the organisers' answer.
Live evidence comes from the rehearsals below.

### 4.2 Before Sunday 17:00 UTC

- **PUB A cases** on the A-only agent, and **PUB B cases** on the A+B agent, each with `--reps 3`.
  - `tests/cases/a-under-b.json` copies the A cases with the notes B8 requires added. The A+B agent writes a note on
    PUB-05 and PUB-08 by design.
- **Styles:** all five `--style` values. Per style, the A-only agent runs `--brief A` (10 cases) and the A+B agent runs
  `--brief B` (3 cases). Target ≥12/13 combined. Running all 13 on one agent cannot pass: the A+B agent writes B8
  notes on A cases, and the A-only agent has no multi-product rules.
- **`tools/rehearse.py`** wraps `run_cases`:
  - takes custom case files: PUB case ids, so the mock's customers and faults apply, with our own scripts, checks
    and expected ledgers;
  - can override `now`, so the same customers run on Fridays, before holidays and after hours.
- **Mock hidden briefs.** Each mock's expected ledgers come from a **separate LLM pass over the mock brief text**
  (`tools/expected_from_brief.py`, which imports nothing from `builder/`), never from our engine. The user reviews
  them.

| Mock | Form | What changes | How tested |
|---|---|---|---|
| M1 | clean spec + own openapi copy | Holidays, a calendar-day deadline, bands, attempts, callback window, several `now` days (outcome/reason codes stay the mock's: it rejects others), and **one flow rule outside the schema** (e.g. a weekly payday sets the attempt-1 date) | Live, `rehearse.py --reps 3`. It is also the D-Z comparison |
| M2 | M1 as mail + policy extract + amending memo + code list, in one directory with its openapi | The memo changes some values. Codes come only from the list | `--until spec` diff against M1: empty on facts the memo does not touch (never cut). The live rehearsal may be cut only if that diff is clean; it is mandatory if the diff is not clean |
| M3 | API-shape mutation | Renamed request and response fields, `postcode`/`birth_year` questions, cents amounts, no `title`, renamed outcome/reason codes | Offline: build, stub conformance, local replay, chat smoke. Live too, as a bonus, if the organisers confirm custom customers |

---

## 5. Trace

Output:
- `traces/<slot>-<UTC yyyymmdd-hhmmss>.jsonl`, one event per line;
- the same events on stdout (the organisers `tee` it);
- a human-readable summary block at the end.

| Event | Fields |
|---|---|
| `run_start` | t, argv, inputs (path, kind, bytes, sha256), builder git commit, python version |
| `model_call` | t, model, purpose (one line), seconds, input/output tokens, ok, attempt |
| `stage` | t, name, seconds, summary |
| `spec` | rule count, coverage %, amendments applied, `not_specified` fields, residuals by class |
| `check` | name, ok, details |
| `freya` | action (validate, create, update, publish, readback, export), workflow_id, agent_id, version, node count, tool counts by type |
| `self_test` | name, result, details |
| `warning` | text |
| `run_end` | t, seconds, status, outputs |

```json
{"t":"2026-10-11T17:03:12Z","ev":"model_call","model":"claude-opus-5-5","purpose":"digest: consolidate brief material and apply the amending memo","seconds":148.2,"tokens_in":21400,"tokens_out":6900,"ok":true}
{"t":"2026-10-11T17:11:40Z","ev":"freya","action":"publish","workflow_id":"wf_…","agent_id":"ag_…","version":3,"nodes":19,"tools":{"lifecycle":1,"pre_actions":6,"code":2}}
```

**Redaction** before every write: the values of `FREYA_API_KEY`, `ANTHROPIC_API_KEY` and `FREYA_BANK_TEAM_KEY`, and
the patterns `fak_[A-Za-z0-9]+` and `sk-ant-[A-Za-z0-9-]+`. A unit test checks the redaction.

---

## 6. Rule enforcement map (Brief A and Brief B)

**Key for "Enforced by":**

| Code | Means |
|---|---|
| code | brain logic |
| route | deterministic graph structure |
| ext | extraction field |
| tts | fixed sentence from a template |
| end | end-node farewell |
| life | lifecycle `close_call` |
| api | engine-run api node |
| lint | build-time check |
| prompt | llm twin or free-mode prompt |

### 6.1 Brief A

| Rule | Enforced by | Where | Risk / note |
|---|---|---|---|
| §2 call variables | code, api | `bindings.call_vars`; customer id baked into every body; `now` read by a tolerant parser | Format of `now` |
| 1 First sentence: digital assistant, recorded; no collections/debt/payment/product before verification, for everyone | tts, route, lint | Opening tts from the spec. Before verification `say_next` comes only from pre-verification templates and `facts` is empty. Policy lint over all pre-verification text | The llm twin before verification has no facts. Its prompt states the ban in general terms without listing the words |
| 2 "Am I speaking with {title} {last_name}?" | tts | Opening template via bindings | — |
| 3 What counts as confirmation; ask once more; still unclear → not_reached/unclear; a question is answered first and the next ask is the second | ext, code | `open.main`; `side_q`; the brain counts asks, answers the question, then makes the second ask; regex fallback on `said` | A missed extraction re-asks without counting |
| 4 Robot/AI → digital assistant, continue | ext, tts | `side_q=robot` → phrase before the current ask | — |
| 5 Someone else: say nothing beyond the opening; ask when reachable | code, tts | Third-party templates only; `facts` empty | — |
| 6 Callback today or next business day, 10:00–19:00 inclusive, later than now → schedule_callback → not_reached/callback | ext, code, api | `third.main=gives_time` + date/time slots; callback validator from the spec model; `api_schedule_callback` with converted date and time | "around eleven" parsed as 11:00. Missing time (`no_time`) is not the same as outside the window |
| 7 Outside window: ask once more; still outside → no_callback, nothing scheduled | code | `re_asks` counter | — |
| 8 Not the customer's number → wrong_number | ext, code | `open.main` or `third.main` = wrong_number → situation | "She's not here" is not a wrong number (descriptions taken from the rule text) |
| 9 Deceased: exact sentence, ask nothing, close | ext, code, end | `open.main` or `third.main` = deceased → situation → `final_*`; the farewell (sentence + goodbye) is spoken by the end node's terminal message | — |
| 10 Relative may not answer security questions or hear anything, even if offering to pay | route, code | Identity is reachable only after confirmation. The offers-to-pay event gets a template | — |
| 11 Two questions, one at a time, date of birth then phone; verify each; date as YYYY-MM-DD; numeric date read day-month-year | ext, code, api | `identity.questions`; digit groups + month word; `day_first` normalizer; completeness checked before calling; `api_verify_identity` | Pieces and in_words styles |
| 12 A wrong answer may be asked again; 3 wrong in total ends the check | code | `on_wrong=repeat`; `wrong_left` read through its binding | — |
| 13 `day_month_swapped` → ask the month as a word; not counted | code | Hint role, `counts_as_wrong=false` | — |
| 14 After 3 wrong: no debt, app or branch, not_verified/failed | code, end | Situation farewell | — |
| 15 Decline: explain once; still declining → declined; asking what the call is about counts as declining; second refusal closes | ext, code | `identity.main=refuses`, `side_q=purpose` → decline counter with the spec's scope | Clash with the asks_back style (open question) |
| 16 get_account after both questions pass | route | The brain routes to `api_get_account` only when the `fully_verified` role is true | — |
| 17 "collections team", late payment, product in words, past-due amount, days late | code, tts | Disclosure template; product naming per type via response bindings | — |
| §7 Business days, holidays | code | `calendar` | — |
| 18 Deadline and persuasion attempts by band | code | `bands[]`: selector, deadline expression, attempts | — |
| 19 Promise date ≥ today, a business day, ≤ deadline | code | Date validator | — |
| 20 Weekend or holiday before the deadline → next business day; after the deadline → rule 23 | code | `non_bd_shift=next` | — |
| 21 Vague period → ask once for the day; still none → propose the deadline | ext, code | `named_periods`, vague flag, `vague_policy` | "Salary on 6 November" is a date, not vague |
| 22 "next/this <weekday>" | code | Weekday semantics; date parts resolved against `now` | — |
| 23 After the deadline → state the latest date and offer it; refusing it is a flat refusal | code, tts | `after_deadline_policy` | — |
| 24 Ask for the full amount first; never "how much can you pay"; the agent proposes every amount | code, tts | `first_ask` amount = full; `amount_elicitation=agent_proposes`; no template asks for an amount | llm twin prompt forbids it |
| 25 Floor = minimum_payment (card) / instalment_amount (loans) | code | Floor expression per type via response bindings | Renamed fields are handled by the bindings |
| 26 Too much → floor once, "the rest stays overdue"; refusing the floor is a flat refusal; attempt 2 may name the floor again | code | `too_much_policy`, `floor_offered` flag, ladder | PUB-06 "offered once": attempt 1 after a floor offer is phrased as a date proposal |
| 27 Floor ≤ amount ≤ full → accept; below the floor → cannot record, offer the floor once; refusal is flat | ext, code | Amount validator; `below_floor_policy` shares `floor_offered` | Amounts in words |
| 28 "Can you make this payment today?" | tts | `first_ask` template | — |
| 29 A proposed date or amount is never a refusal | code | Precedence: a slot beats a refusal | — |
| 30 Flat refusal or non-answer uses an attempt; attempt 1 = last business day of this week (next week if today is that day), full amount or the floor after "too much"; attempt 2, late band only = floor on the deadline | code | `ladder[]`, `attempt_consumption`; the date is clamped by the rule 19 validator | An empty extraction is not a non-answer the first time |
| 31 Attempts used up → stop; no refused offer repeated; no_promise/refused | code | `dedupe_refused_offers`; situation | — |
| 32 Hardship: one sentence of sympathy, no persuasion, offer support; accepts → transfer support / transferred; declines → no_promise/hardship; comes before 30 and 41; a date or amount the customer still names is recorded | ext, code, tts, api | Event `hardship`, `suppresses` [persuasion, restructuring], precedence; sympathy by tts | PUB-05's discount request must not trigger restructuring |
| 33 No discount, waiver, plan or other terms | tts, lint, prompt | No template contains such terms (policy lint over `forbidden_offer_terms`); llm twin and free-mode prompts forbid them | **Prompt-only remnant** for the llm twins and free mode |
| 34 Read back product, amount, date; "Do you confirm?"; create_promise only after a clear yes | code, tts, ext, api | Confirm stage; `confirm.main`; write chain only after yes | — |
| 35 Change at confirmation → re-check, read back again | code | Validators, then a new read-back | "Yes, but Thursday": the change wins |
| 36 One promise per product; no change after recording → mobile app | code, tts | Recorded-set gate; change-after-record sentence | — |
| 37 "Recorded" only after ok:true; retry once; then "could not record" and no_promise/service_error | code, api | `failure_policy`; `say_ok`/`say_fail`; ok role | 500 vs ok:false detection (spike S2b) |
| 38 "You will receive a text message confirming this promise." | tts | `post_promise_sentence` | — |
| 39 Already paid → may take one business day to appear; no promise; close paid | ext, code, tts | Event `paid` → situation (Brief B: note, then continue) | Confusable with rule 40's "paid off in the past"; descriptions come from the rule text |
| 40 "Not mine" / never had it / closed or paid off in the past → no argument, no promise, no_promise/dispute | ext, code | Event `dispute` | — |
| 41 New plan or lower instalments → do not describe a plan; transfer restructuring | ext, code, api | Event `restructuring`; precedence over too_much | PUB-09 |
| 42 Human: ask once whether you can help; insists → transfer general / transferred/human; before and after verification | ext, code, api | Event `human`, `ask_once_first`, stage any | — |
| 43 "Is this a scam?" → verify by calling customer service; continue | ext, tts | `side_q=scam` → phrase | — |
| 44 A question the tools cannot answer → say so; no invented figures | tts, prompt | `side_q=other` → llm twin with facts, or the fixed "no information" phrase | **Prompt-only remnant** |
| 45 Says twice they don't want to talk → no_promise/refused (verified) / not_verified/declined (before) | ext, code | Two `no_talk` event rows with `min_occurrences: 2`: stage pre → close(not_verified/declined), stage post → close(no_promise/refused) | Staleness is handled by evidence dedupe |
| 46 Exactly one close_call, last, outcome + reason from the table | life, code | Lifecycle `close_call`; `final_*` from situations + aggregation; pre-hook for the reason | Spikes S2 and S3 |
| Outcome table | lint | `situations[]` validated against the close_call enums | — |
| A transfer after a recorded promise closes as transferred | code | `closing.aggregation.transfer_overrides_promise` | — |
| "Brief A never uses add_note" | route | `notes.enabled=false` → no `api_add_note` node | — |
| 47 Thank and say goodbye before close_call | end, life | Farewell template; close_call runs after the call | — |

### 6.2 Brief B

| Rule | Enforced by | Where | Risk / note |
|---|---|---|---|
| 1 One to three products, each with its own past_due, days_late, floor | code | Products role list via bindings | — |
| 2 State every product, one sentence each, before asking anything | code, tts | Disclosure per product | — |
| 3 Band and deadline per product | code | Per-product band | — |
| 4 Ask whether all can be paid today; if not, one by one in get_account order; no today-question per product; each starts at attempt 1 | code | `first_ask.scope=all`, `product_order`, ladder per product | — |
| 5 Attempts counted per product | code | `attempts_scope=per_product` | — |
| 6 One date for all, after one product's deadline → that product on its own deadline, said in the read-back | ext, code, tts | Slot `scope=all`; `overflow_policy=shift_to_own_deadline_and_say` | Scope extraction; a date for all that falls on a holiday |
| 7 One read-back for all, one confirmation, create_promise per product | code, api | `confirm_mode=all_at_once`; promise chain | — |
| 8 add_note with product_id and reason for every product without a promise, also when none has one | code, api | `notes`; flushed in the closing turn | A hang-up before the flush loses notes |
| 9 Dispute or "already paid" on one product → note it, continue | ext, code | Event action `note(current)` + continue; product refs | — |
| 10 Restructuring still transfers; recorded promises stay; agreed but unrecorded ones are not recorded; no notes at all on any transfer | code | `notes.on_transfer=drop`; the transfer happens before the promise chain | — |
| 11 A create_promise that fails twice → keep the others, note service_error | code, api | `failure_policy` for create_promise (per tool) with retry counters per product in brain state; when exhausted with several products, the situation carries note_reason service_error and the chain continues; `closing.aggregation` then gives promise or no_promise/service_error | — |
| Closing: all noted paid → paid; otherwise no_promise with the first non-paid note reason in get_account order; promise wins except rule 10 | code, life | `closing.aggregation` | Note reason ≠ close reason is handled by the mapping |

### 6.3 Walk of the 13 PUB cases

`now` = Wed 2026-10-14 11:00. Late band deadline = 7th business day = **2026-10-23**. Early band deadline = 12th
business day = **2026-11-02**, because 26 Oct is a holiday.

| Case | Path through the brain | Ledger | Depends on |
|---|---|---|---|
| PUB-01 | confirm → DOB 1988-03-14 → phone 4471 → get_account → disclosure + today? → yes → read-back → yes → create | (p1, 420.00, 2026-10-14), promise | `open.main=confirmed`; DOB parts |
| PUB-02 | someone_else (+ purpose question, offer to pay) → "only with Ms Costa; when can she be reached?" → "tomorrow around eleven" → 2026-10-15 11:00 valid → schedule | callback (2026-10-15, 11:00), not_reached/callback | third party vs wrong number; time parse |
| PUB-03 | DOB wrong ×3 (DOB re-asked each time) → wrong_left 0 → app/branch farewell | not_verified/failed, no get_account | re-asking DOB, not moving to phone |
| PUB-04 | car loan, late → today? → "salary 6 Nov" → after the deadline → "latest is Friday 23 October" → robot question → phrase + offer → accept | (p1, 1250.00, 2026-10-23), promise | 6 Nov parsed as a date; side_q |
| PUB-05 | today? → job loss → one sympathy sentence + support offer → discount request → refused without restructuring → declines transfer | no_promise/hardship | **hardship precedence** |
| PUB-06 | late → today? → too much → floor 637.50, rest stays overdue → "this Friday" (or refusal → attempt 1 = floor on Fri 16) | (p1, 637.50, 2026-10-16), promise | floor named once |
| PUB-07 | early → can't today → attempt 1: 540 on Fri 16 → "week after next" → vague → ask once → "Monday the 26th" → holiday → offer Tue 27 → accept | (p1, 540.00, 2026-10-27), promise | vague flag; weekday + day of month |
| PUB-08 | paid event → "can take one business day" → close | paid, no note (A-only agent) | — |
| PUB-09 | restructuring beats too_much → transfer | transfer queue restructuring, transferred/restructuring | precedence |
| PUB-10 | late → can't today → attempt 1: 365 on Fri 16 → yes → read-back → yes → create fails → retry → ok | (p1, 365.00, 2026-10-16), promise | failure detection (S2b) |
| PUB-11 | all today? → no → p1 attempt 1 Fri 16 → "all three this Friday" (scope=all) → all within their deadlines → one read-back → 3 creates | (p1, 220.00), (p2, 640.00), (p3, 75.00), all 2026-10-16, promise | scope=all |
| PUB-12 | DOB ok → phone 4401 wrong → phone re-asked only → 4410 → 3 product sentences → all today? → "card tomorrow, not the others" → p1 2026-10-15; p2 attempt 1 (1320 on Fri 16) ✗, attempt 2 (440 on Fri 23) ✗; p3 attempt 1 (140 on Fri 16) ✗ → read-back p1 → create → notes p2, p3 refused | (p1, 510.00, 2026-10-15), notes refused ×2, promise | p1-scoped date in a multi-product turn |
| PUB-13 | both today? → "no, both on 30 October" (scope=all) → p1 early 10-30 ok; p2 late capped at 10-23 and said → one read-back → 2 creates | (p1, 300.00, 2026-10-30), (p2, 900.00, 2026-10-23), promise | without scope, rule 23 gives the same ledger |

---

## 7. What changes in a hidden brief, and how the design absorbs it

### 7.1 Absorption table

| Likely change | Absorbed by |
|---|---|
| Other identity questions (postcode, birth_year, company_number, founded_year), other order or count | `identity.questions[]` from brief + openapi enum; normalizer pipeline; `complete_pattern`; extraction slots generated per question |
| Answers in two parts, spoken digits ("oh", "double five"), letters, leading zeros | Accumulation across turns + completeness check before calling; digit-word normalizer |
| Other limits: total 2, per-question limit, move on after a wrong answer | `limits.total/per_question`, `on_wrong` |
| New hints (e.g. a partial answer) | `hints[]` with `counts_as_wrong` |
| Renamed request fields (`customer_ref`, `product_ref`, `pay_on`, `question`/`value`) | `bindings.tool_request` |
| Cents amounts, merged `callback_at` | Converters `cents_int`, `datetime_join` |
| Renamed response fields (`passed`, `attempts_remaining`, `accounts[].overdue_amount`) | `bindings.tool_response`, validated against the response schema |
| New required body fields (transfer reason, note text) | Binding sources: literal, situation field, template |
| No `title`; business customer with a company name | Free opening template over declared call variables |
| Other bands, more bands, a band chosen by product type, an over-N band that transfers | `bands[]` with selector expression and action |
| Calendar-day deadline rolled back, month-end with a minimum, counted from the due date | Deadline expressions over the helper library |
| Floor formulas (max of a field and a constant, a percentage rounded up) | Floor expressions; `round_half_up`, `ceil_to` |
| Holidays by name; other business weekdays | Digest gives explicit dates; `calendar` |
| Effective dates in a memo | `effective_from`, resolved in code against `now` |
| "Next Friday" = nearest upcoming; weekend rolls to the previous day; no "today" after a given hour | Weekday semantics, `non_bd_shift`, `earliest_date` expression |
| Named periods ("end of month" defined, "payday" vague) | `named_periods[]` feed the extraction enum |
| Other callback rules (next 2 business days, lunch break, half-hour slots, minimum lead) | Callback model: `day_set`, `windows[]`, `granularity`, `min_lead` |
| Other first ask, other ladder, other floor policy | `first_ask`, `ladder[]`, `too_much_policy`, `below_floor_policy` |
| Per-product confirmation; re-asking a product whose date is past its deadline | `confirm_mode`, `overflow_policy` |
| Other product order | `product_order` expression |
| Hardship with a different persuasion rule or queue | Event row with `persuasion` mode and queue |
| New events: lawyer/insolvency, a verified customer wants a callback, "already paid → when?" | Open action set: ask + branch, callback, note(all open), close |
| Another outcome table; codes only in a code list; free-string reason | Open `situations[]`; code-list mapping; verbatim check |
| Priority-based closing; note reason ≠ close reason | `closing.aggregation`; note → close mapping |
| Notes on single products, note text required | `notes.min_products`, text template |
| Faults on get_account, transfer or callback | Per-tool `failure_policy` |
| Several calendar days | All dates computed in code from `now` at call time |
| A rule nobody anticipated | Classified; speech → prompts; flow → extension point or free mode with code gates; always reported |
| Raw formats (.eml, csv, a directory) | Directory + several files now; `.eml`/zip/HTML below the cut line |

### 7.2 Red-team evidence

Two invented hidden briefs were pushed through the first draft. They exposed most of the rows above.

**H1, clean: a retail bank.**
- Call variables: `customer_ref`, no `title`.
- Identity: `postcode` + `birth_year`, a per-question limit, and a `postcode_partial` hint.
- Calendar: a calendar-day deadline rolled back to a business day; month-end with a minimum; an over-60 band that
  transfers.
- Rules: "next Friday" means the nearest one; an overdraft floor of 20% rounded up; per-product confirmation;
  priority-based closing.
- API: cents amounts and `callback_at`.

**H2, raw: an SME bank.**
- A `.eml` mail that marks sections to ignore and contains a non-binding wish.
- A policy extract with holidays by name and a director clause.
- A memo that changes values and withdraws a clause, with an effective date.
- `codes.csv` holding every outcome, reason, queue and note code, and one back-office-only code.

### 7.3 Leak guard

- No Brief A/B values in `builder/` code or prompts. `tools/leak_lint.py` extracts candidate literals from
  `briefs/*.md` (dates, backticked identifiers, quoted sentences, field names) and greps `builder/`.
- Few-shot examples come from a synthetic non-bank domain.
- No engine defaults (§2.5).
- Tests may hold Brief A/B data. They live in the top-level `tests/` (`tests/unit/`, `tests/fixtures/`,
  `tests/cases/`), never under `builder/`. The builder never reads `tests/` at run time.

---

## 8. Risks, spikes, fallbacks, Plan Z, alternatives

### 8.1 Platform behaviour the hub relies on

Condensed from the platform review. DV = documented and verified; DU = documented, unverified; UD = undocumented;
CX = contradicted.

| # | Behaviour | Status | Spike |
|---|---|---|---|
| D03/D04 | When a talk node's `always` fires, no dialogue call is made there; the target's on_enter speaks; a node entered mid-turn does not cascade | DU/UD | S1 |
| D07 | `{{say_next}}` re-renders on re-entry with a value written in the same turn | DU | S1 |
| D08 | Passthrough nodes can be revisited many times in one turn | UD | S1 (12 hops) |
| D09 | Transitions to the same target merge; an unconditional one wins | documented trap | lint |
| D10 | Non-conversation nodes default to `on_enter: llm` | documented | lint |
| D12/D32 | Extraction re-fills a slot from an older message after code reset it | UD | S1 |
| D14 | What happens when a code snippet raises | UD | S1 |
| D17/D18 | Code size limit; exec latency; is code in the transcript | UD | S1b |
| D19 | api_request bodies baked from vars the previous code node wrote | DU | S2 |
| D20 | Number typing of baked body fields | UD | S2 |
| D22 | Automatic key naming for api_request nodes (node or function name) | UD | S2 (node ≠ function) |
| D23 | 500 envelope; stale `_response` keys after a failure | UD | S2b (PUB-10) |
| D28 | Unresolved lifecycle field: dropped, or filled by a model | CX | S3 |
| D29/D30 | `""` sent as an empty reason; pre-hook on a lifecycle tool | documented / UD | S2 |
| D34 | date/datetime extraction anchors to the real clock, not `now` | CX with the CLAUDE.md tip | S1 (probe only) |
| D36 | `max_node_turns` per entry or per call | ambiguous | avoided (200, no target) |
| D38 | Chat API may skip a first code node and may return 504 | seen | design: tiny `init`, lazy brain |

### 8.2 Spikes and gate G1

| Spike | Tests | Result |
|---|---|---|
| S1 | Chat API, throwaway workflow in slot `scratch`: loop core, re-render, no cascade, 12-hop ping-pong, staleness, date anchor, deliberate raise, custom farewell | PASS/FAIL per behaviour |
| S1b | S1 with the brain padded to 60 KB | Latency per code run, size of `messages`, code visible in the transcript |
| S2 | PUB-01 practice session: verify → get_account → create_promise chain from code vars; node ≠ function naming; amount type; mapped product list; lifecycle close with sentinel and pre-hook | Request bodies, key names, ledger |
| S2b | PUB-10 session: the 500 on create_promise | Envelope, counters, stale keys, exactly one promise |
| S3 | Lifecycle variants: reason never set; a hang-up right after the opening | Whether a model fills the field or it is dropped; close on hang-up |
| *(cut)* S4 | Mini hub in a real simulation (PUB-01, PUB-02) | Superseded by the walking skeleton |

**G1 (Sat 15:30 UTC).**

| Spike result | Design |
|---|---|
| All green | Hub as designed |
| Re-render or cascade fails | L1 |
| Hop limit < ~10, or a code run > 1.5 s, or bad bloat | L2 |
| Per-turn code nodes unusable | L3 |

**K1 (Sat 21:00 UTC).** The walking skeleton must pass `--only PUB-01,PUB-02,PUB-03` live. It may run on a
hand-written fixture spec, because K1 judges the hub runtime on Freya, not the extractor. If it fails, switch to L3
then, not on Sunday.

### 8.3 Fallback ladder (all on the same `policy_lib`)

- **L1, patched hub.**
  - Twin talk nodes per stage that the brain alternates, so every entry is fresh.
  - Talk transitions guarded by `current_turns`.
  - api nodes named after their functions.
- **L2, precomputed branches.**
  - The brain runs once per user turn. It writes the bodies of up to k sequential calls and one utterance per result.
  - Linear api chains route on their own mapped ok to tts nodes.
  - The brain reconciles on the next turn.
- **L3, guarded stages.**
  - Stage conversation nodes where the model leads the conversation.
  - A code node at stage entry writes fact sheets.
  - Gate code nodes validate customer proposals.
  - Model-called verify/transfer tools with pre-hooks (normalise) and post-hooks (`what_to_do_next`, state).
  - Engine-run promise, note and callback writes.
  - The same lifecycle close.
  - About 5 hours on top of `policy_lib`.

### 8.4 Plan Z (independent safety net)

- **What it is.** The kit's agentic builder (`examples/agent_builder.py` + skill), run unmodified on A and A+B now
  for a baseline. Its `--reps 3` scoring runs in the overnight queue.
- **For hidden briefs:** `planz/build.py {brief_path} {tool_api_path}` wraps it.
  - It accepts several files and directories, and finds the tool API file by its `openapi` key.
  - It uses a **blind** copy of the skill: read the given openapi file, run no PUB tests, export
    `submission/<stem>-z.workflow.json`.
  - It writes a trace with the CLAUDE.md fields: start and end time, every model call (model + one-line purpose),
    workflow id, agent id, node and tool counts, self-tests and their results.
  - Its slot is `<stem>-z`, or `BUILDER_SLOT`.
  - It shares no code with `builder/`, so a bug in `policy_lib` cannot take down both.
  - It is tried on M2's raw-material directory before D-Z.
- **D-Z (Sun 13:00 UTC)** compares both builders on the **M1 live rehearsal** (`--reps 3`), with Plan Z built blind.
  It does not compare on PUB scores: Plan Z tunes itself on the PUB cases during its own build.
  - The higher M1 score wins. A tie goes to ours (deterministic, faster build: the second tie-break).
  - The form has one `run_command`, so the winner serves both hidden briefs.

### 8.5 Alternatives considered

- **Agentic first** (an LLM writes the workflow, tests and repairs it).
  - It is flexible, and the kit says it passed 10/10 on Brief A once.
  - Its output varies per run. Its repair loop needs ledger tests that do not exist for hidden briefs. Every rule
    stays a prompt rule for a weaker model under 3/3 scoring.
  - Kept as Plan Z.
- **Guarded stages first** (prompt-centric with code gates).
  - Faster to a first green run, with more natural speech and graceful handling of rules nobody modelled.
  - The rules the judge counts (attempts per product, floor offered once, vague asked once, second refusal) would
    live in prompts. Illustration: at 95% per decisive turn over 6 turns, a 3/3 pass is about 0.40, against about
    0.70 at 98%.
  - Kept as L3, and its strengths are grafted onto the hub: llm twins, free mode, stage rules in prompts.
- **Why the hub first.**
  - Decisions are exact and replayable offline. Only one narrow per-turn extraction varies.
  - The two main risks are bounded: platform mechanics by G1 and K1, extraction quality by the multi-field contract
    and the styles run.
