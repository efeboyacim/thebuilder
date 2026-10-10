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
