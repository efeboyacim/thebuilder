---
name: build-phone-agent
description: Build a Freya phone agent from a bank's brief and test it in chat. Use when asked to build a phone agent from a brief.
---

# Build a phone agent from a brief

Work from this folder. Use only what the brief files and the tool API file say. There are no practice customers and no
test cases: you test by chatting with the agent yourself.

## Steps

1. Read every brief file you were given (in `briefs/`, in the order given; a later file or a later section such as a
   memo can change an earlier one), then `tools/openapi.json`.
2. Read `docs/workflow-format.md`, `docs/tools-and-code.md` and `docs/api.md`.
3. Read `examples/base-agent.json`. It is a small working agent. Use it as the pattern for the JSON, not as the answer.
4. Write the workflow to `out/<slot>.workflow.json` (`<slot>` is given in your task).
   - One step per stage of the call. Identity comes first.
   - Move to the next step on a tool result, not on what the customer says.
   - Take every tool field and allowed value from `tools/openapi.json`. Its server URL is the bank.
   - Compute dates, deadlines and amounts in code from the call variable `now`, never from the real clock.
   - Leave `agent_config` out.
5. Save it: `python3 -c "import freya,json,sys; print(json.dumps(freya.save_workflow(sys.argv[1], json.load(open(sys.argv[2])))))" <slot> out/<slot>.workflow.json`
   It prints `workflow_id` and `agent_id`. The same slot updates the same workflow. Save a new slot twice.
6. Test it in chat: `python3 tools/chat.py <workflow_id> "<customer turn 1>" "<customer turn 2>" ... --name <what it tests> --now <a date and time>`.
   You play the customer. The customer id is made up, so identity checks always fail and nothing is written to the
   bank: test the opening sentence, someone else answering, a question before confirming, a refusal of the security
   questions and wrong answers. Run at least four such conversations, on different `--now` days. Never use any other
   way to test (no simulations, no practice sessions).
7. Compare each reply with the brief. Change the workflow file, save it again, chat again. Do this at most three times.
8. Finish with one line: the `workflow_id`, the `agent_id` and what the chat tests showed.

## Rules

- Never edit the agent on the platform by hand. Change the file and save it.
- When a tool call fails, the agent must say so. It must never tell the customer something is recorded when it is not.
- Prefer rules in routes and code over rules in prompts.
