# Platform spikes

## S1 + S1b (item 03): the hub loop on the chat API

Run on 2026-10-10 in slot `scratch` (the base agent is restored after each run). The final results come from run 4
(14:55 UTC) and an S1b rerun (14:59 UTC), after the platform fix. Runs 1 to 3 (14:36–14:52 UTC) hit slow code runs
and sandbox outages; they are kept for the failure behaviour they showed.

```bash
python3 spikes/s1_loop.py              # S1 then S1b. --no-hop skips the 12-hop turn; --only s1|s1b|probe
```

The spike workflow is a small hub:

- `init` (code) → `opening` (tts) → `brain` (code).
- `brain` routes on `route` to:
  - `talk_a` / `talk_b` (tts `{{say_next}}`);
  - `hop` (code);
  - `crasher` (a code node that raises);
  - `end_custom` (end_call, farewell `{{farewell}}`).
- Every talk node has `always → brain` and a task_message marker that would show a dialogue call.
- S1b is the same with the brain padded to 60 KB.

Raw results are in `spikes/results/`.

### Results

| Behaviour | Result | Evidence |
|---|---|---|
| D03 loop core talk → brain → talk | PASS | "Pong 1, brain runs 1." in every run |
| D04 no dialogue call in a talk node whose `always` fired | PASS | One assistant message per turn; the marker never appeared |
| D04 no cascade | PASS | Exactly one brain entry per plain turn |
| D07 `{{say_next}}` re-renders on re-entry | PASS | `talk_a` re-entered from itself spoke the new text ("Again 2", "Pong 3") |
| D08 12 hops in one turn | PASS | 13 brain and 12 hop entries; "Hop count 12" after 48 s (run 4), about 125 s in the slow run 2. The chat route returns 504 at 60 s, while the engine finishes the turn |
| D12 extraction refills a slot that code reset | PASS | After the brain reset `code_any` and `code_last` to `""`, two later turns extracted only `cmd` and `said`. Neither slot was written, not even with `none`, with or without "LAST message" in the description |
| D14 a raising code node | PASS | `crasher` raised; its transition on `crasher_failure_count > 0` fired, `crasher_response.status` was `error`, a variable written before the raise was kept, and the next turn ran normally |
| D34 `date` type anchors to the real clock | confirmed | With `now` = 2026-11-25, "tomorrow" was extracted as 2026-10-11, the real clock's tomorrow |
| Custom farewell from code | PASS | `end_custom` spoke `{{farewell}}` as the brain built it |
| `endedReason` = terminal `function.name` | FAIL on the chat route | `assistant-ended-call`. The farewell turn also returned 504 after 15 to 21 s, with the farewell already in the call's messages |
| D38 a first code node on the chat route | skipped | `opening` spoke the literal `{{init_ran}}` in every run |
| S1b loop with a 60 KB brain | PASS | Same answers as S1 |
| S1b code run time | 2.25 s median (4 runs) | The same as the 2 KB brain measured at that time (1.5 to 2.4 s) |
| Code in the transcript | yes | Every `function_call_start` event carries the whole code string. A 60 KB brain adds about 66 KB to the call's `messages` per run. Whether the dialogue model sees it is not known |

### Code run time

Seconds from `function_call_start` to `function_call_complete` of one `code_exec`.

| When (UTC) | Run | Median | Range | Runs |
|---|---|---|---|---|
| 14:36 | Run 1, 2 KB | 5.49 | 4.7–8.1 | 14 |
| 14:44 | Run 2, 2 KB | 4.35 | 3.2–7.9 | 28 |
| 14:50 | Run 3, 2 KB | 7.70 | 6.3–8.2 | 5 |
| 14:55 | Run 4, 2 KB | 2.33 | 0.1–7.9, falling during the run | 36 |
| 14:59 | S1b rerun, 60 KB | 2.25 | 1.5–2.4 | 4 |

Extraction took 0.25 to 0.7 s per turn. The user reported that the slow runs were a platform issue that has been
fixed.

### Sandbox outages (runs 1 to 3, platform issue)

- **The error:** `Failed to reach snekbox: Cannot connect to host snekbox-svc.staging.local:8060 ssl:default [Name or service not known]`.
- **When:** about 14:38, 14:48 and 14:51 UTC. Each time it was back within 1 to 2 minutes.
- **A failed run is silent.** The node's transitions are evaluated on the old variable values. The old `say_next` was spoken again, turn after turn. In run 1 a stale `route = hop` kept brain and hop looping into the next turns.
- **A failed run is detectable on the next transition.** It sets `<node>_response.status = error`, `<node>_status_code = 502` and `<node>_response.error`, and increments `<node>_failure_count`. All but the cumulative count are overwritten on every run. A successful run sets `success` and 200.
- **Plan Z's hook failure in item 02** ("did not return a parseable result") fell in the same afternoon and is likely the same sandbox.

### Consequences for the design

1. **G1, S1 part: the hub stays.** Every S1 behaviour the hub relies on passed.
   - Code-run time after the fix (1.5–2.4 s) is above the G1 table's 1.5 s line.
   - The line stood for the 8 s turn budget, and that budget holds: a two-run turn (identity answer, non-binary result) costs about 2 × 2.4 s + 2 HTTP calls + extraction, about 6 s.
   - L2 only if the median code run exceeds 3 s.
2. **The brain's first transition** is `brain_response.status equals error` → `talk_error`. `talk_error` speaks a literal text, so a failed run never replays the old `say_next` nor follows the old `route`.
3. **Nothing spoken before the brain's first run** may use a variable that `init` sets, because the chat route skips `init`. The brain initialises lazily.
4. **No `date`/`datetime` extraction**, as decided: D34 is confirmed.
5. **Keep the brain at ≤15 KB**, even though size does not slow it: the code is copied into the transcript on every run.
6. **The chat self-test (item 23)** reads a turn's reply from the call's messages when the chat route returns 504. That happens on long turns and on the farewell turn.
7. **Nothing scored depends on `endedReason`.** On the chat route it is `assistant-ended-call`, not the terminal's name.

## S2, S2b, S3 (item 04): engine-run bank calls and the lifecycle close

Run on 2026-10-10 in slot `scratch` (the base agent is restored after each run), on fresh practice sessions:
S2 on PUB-01, S2b on PUB-10 (its `create_promise` fails once), S3 on three PUB-02 sessions. The final results come
from run 2 (15:15 UTC). Run 1 (15:10 UTC) is kept for two failures it showed: a mapped list never lands, and an
unresolved lifecycle field is filled by a model.

```bash
python3 spikes/s2_api.py               # S2, S2b, S3. --only s2|s2b|s3; --keep leaves the spike in the slot
```

The spike workflow:

- A code `brain` routes on commands from the customer's message to api_request nodes. Each node's name differs
  from its function: `n_verify` → `verify_identity`, `n_account` → `get_account`, `n_promise` → `create_promise`,
  and `n_timeout` → `get_account` with `timeoutMs: 1`. Every api node goes `always` back to the brain.
- Before routing, the brain writes the body values (`v_field`, `v_answer`, `v_product`, `v_amount` as a Python
  float, `v_date`). It resets the node's mapped variables to `""`, snapshots the node's counters and sets
  `pending`. On re-entry it reads the result.
- `n_verify` carries a pre-hook and a post-hook. The post-hook adds `data.hook_mark`.
- `close_call` is a workflow-level lifecycle tool with baked `{{final_outcome}}` and `{{final_reason}}` and a
  pre-hook. The run-2 hook rebuilds `outcome` and `reason` from the call variables:
  - `final_outcome` unset → the default pair (`not_reached`, `unclear`);
  - `final_reason` equal to `-` or unset → no reason.
- The bank's ledger is the ground truth for what was sent. Raw results are in `spikes/results/` (`s2-*`, `s2b-*`,
  `s3-*`, `s2-run*.log`).

### Results (run 2: 14 PASS, 0 FAIL, 6 INFO)

| Behaviour | Result | Evidence |
|---|---|---|
| D19 api bodies baked from vars the brain wrote in the same run | PASS | Ledger args `{"field": "date_of_birth", "answer": "1988-03-14"}`, `{"field": "phone_last4", "answer": "4471"}`, `{"date": "2026-10-14", "amount": 420, "product_id": "p1"}`; `customer_id` baked from the call variable |
| D20 a Python float written by code, baked into a `number` field | PASS | Freya sent `"amount": 420.0`; the ledger holds the number `420` |
| D22 automatic key naming, node ≠ function | **node name** | `n_verify_response.data.*`, `n_verify_success_count`, `n_verify_failure_count`, `n_verify_status_code`. No `verify_identity_*` key exists. The event log names every pre_actions call `api_call`; only its `args.url` tells the tool |
| Mapped list (`data.products` → `acct_products`) | **does not land** | The variable kept the brain's `""` in both runs. Scalar mappings (`data.ok`, `data.promise_id`) land |
| Product list from flattened keys | PASS | `n_account_response.data.products.0.product_id` = `p1`, `....0.past_due` = int `420`. Numbers keep their JSON type; booleans arrive as `"true"` |
| Hooks on a pre_actions api node | PASS | Pre-hook and post-hook ran on both calls. The pre-hook saw the baked body, `customer_id` included. A key the post-hook added to `data` reached the node's mapping |
| D23 the HTTP 500 on `create_promise` (PUB-10) | see below | Ledger status 500 with `{"ok": false, "error": "service unavailable, nothing was recorded"}`. Freya logs `function_call_error` "API request failed: …" |
| D23 the failure is visible to code | PASS | `n_promise_failure_count` 0→1, `n_promise_status_code` 500. The mapped `pr_ok` stays at the brain's `""`: no mapping on a failed call |
| D23 the retry succeeds and is visible | PASS | `n_promise_success_count` 0→1, status code 200, `pr_ok` = `"true"`, `pr_id` = `pr1`. Ledger: exactly one promise (p1, 365, 2026-10-16) |
| D23 stale keys | **stale** | After the successful retry `n_promise_response.error` still says `HTTP 500`. Status code and status are overwritten on every call; keys a later response lacks are not cleared |
| Transport failure (1 ms timeout) | INFO | `n_timeout_failure_count` 1, `n_timeout_status_code` 408, `n_timeout_response.status` `error`; mapped variables not written. No ledger entry, so the request never reached the bank |
| Lifecycle `close_call` after the end node | PASS | In the ledger 0.3 s after the farewell (chat route). `chat/end` afterwards does not close a second time |
| D30 pre-hook on the lifecycle tool | PASS | The hook ran: the sentinel reason `-` was removed (`{"outcome": "promise"}`, `ok: true`). The hook reads the call variables (`final_outcome`) |
| D28 unresolved `{{final_reason}}` | **a model fills it** | Run 1: the bank got the reason "The user said 'bye' and the assistant responded with a farewell…" and rejected it. Run 2: the rebuilding hook overrode the fill, so no reason was sent |
| Close on hang-up (`chat/end`) with `final_*` set | PASS | `not_reached` / `unclear`, `ok: true`, about 11 s after `chat/end`; `endedReason` `customer-ended-call` |
| Close on hang-up right after the opening, `final_*` unset | PASS with the hook | Run 1: a model filled `UNKNOWN` / `UNKNOWN`, which was rejected. Run 2: the hook's default pair, `ok: true` |
| First code node in a real simulation | runs | Plan Z's PUB-01 call (item 02): `setup` (code) ran before the opening. Only the chat route skips it (D38) |

### Timing (run 2)

- Code runs: median 0.16–0.22 s, max 1.2 s (24 runs), against 2.3 s in S1.
- A turn that runs brain, api node and brain took 2.2–4.2 s on the chat route.
- The farewell turn took 14–17 s and returned 504, as in S1.

### Consequences for the design

1. **G1: the hub stays as designed.** S1 and S2 are green. No fallback (L1–L3) is needed.
2. **Result keys are named after the api node.**
   - The brain reads `<node>_response.data.<field>`, `<node>_success_count`, `<node>_failure_count` and
     `<node>_status_code`.
   - The compiler emits one api node per tool. It passes the node names to the brain.
3. **A write succeeded** when all of these hold: the node's success count went up by one, its failure count did not
   change, and the mapped `ok` (reset to `""` by the brain before routing) is `"true"`/`"True"`.
   - Code never reads `<node>_response.error`: it goes stale.
   - An HTTP 500 and a timeout both raise the failure count and leave the mapped variables untouched.
4. **Lists come from flattened keys** `<node>_response.data.<list>.<i>.<field>`, read until the first missing
   index. Never map a list.
   - Stale indexes would survive a later, shorter response. So the brain reads `get_account` once, from its first
     successful call.
5. **Numbers.** The brain writes amounts as Python floats. They reach the API as JSON numbers.
6. **Lifecycle close.** `close_call` stays a lifecycle tool, and the two-close-node fallback is dropped. Its
   pre-hook rebuilds `outcome` and `reason` from `final_outcome` / `final_reason`:
   - the sentinel `-` or an empty reason → no reason;
   - `final_outcome` unset → a default pair that the compiler bakes into the hook from the spec's situation for a
     hang-up before the brain ran.

   An unresolved placeholder on a lifecycle tool is otherwise filled by a model with free text, so the hook must
   never trust the body.
7. **`init` sets `final_outcome` / `final_reason`** to the opening's hang-up situation. In simulations it runs
   before the opening; the hook's default pair covers the chat route, where `init` is skipped.
8. **Hooks work on pre_actions api nodes.** That keeps L3 viable, and it allows body clean-up. The hub does not
   need them.
9. **Chat self-test (23).** Identify a bank call in the event log by the `args.url` of its `api_call` start event.
