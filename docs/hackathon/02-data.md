# Data — Thirteen practice cases and the kit

Everything here is synthetic. The bank, the customers, the debts and the payments do not exist.

## Kit files

| File | What it is |
|---|---|
| `briefs/brief-a.md` | Freya Bank's specification for customers who are late on one product. 47 rules. |
| `briefs/brief-b.md` | The extra rules for customers with up to three products. Builds on Brief A. |
| `tools/openapi.json` | The bank's tool API that the phone agent calls. Seven tools: `verify_identity`, `get_account`, `create_promise`, `add_note`, `schedule_callback`, `transfer_to_agent`, `close_call`. |
| `public-test-set.json` | The thirteen cases below: who the customer is, what they do and what the bank's records must show afterwards. (In this repo: `public-test-set.json` at the root, from the kit. `docs/hackathon/public-test-set.json` is the same file.) |
| `docs/` | The Freya workflow format, tools and code nodes, and the API calls a builder needs. Written for a program to read. |
| `freya.py` | A small client for the Freya API. Creates a workflow once and updates it on later runs. Standard library only. |
| `examples/minimal_builder.py` | The smallest builder that works: one step, the brief as its prompt, seven tools. A place to start, not a good score. |
| `env.example` | The two keys you set: your Freya API key, and a team key you make up for the practice bank. |
| `tools/run_cases.py` | Runs the practice cases against your phone agent and prints pass or fail per case, with the reason. |

## How one case runs (diagram, as text)

- **Simulated customer** (follows the case script; text, not audio) ⇄ *conversation* ⇄ **The phone agent your builder made**
- **Phone agent** ⇄ *tool calls* ⇄ **Mock bank API** (identity, debts, promises; we host it)
- **Mock bank API** → **Ledger** (every call and its result)
- **Ledger** → *final state* → **Scorer** (pass or fail per case)
- **Simulated customer** → *transcript* → **Scorer**

The mock bank API stands in for the bank's own systems. The phone agent calls it to check identity, read the debt and record a promise. We host it, so we can see what was really recorded. The scorer reads that record, not what the phone agent says it did. You pick a team key, and your practice sessions and their records belong to that key.

## Practice cases

Ten cases use Brief A and three use Brief B. Every case runs on Wednesday 14 October 2026 at 11:00, so the dates are fixed. Full scripts, expected ledger states and checks are in `public-test-set.json`.

| Case | Brief | Family | What happens | Level |
|---|---|---|---|---|
| PUB-01 | A | Happy path | Confirms identity and pays the full amount today | easy |
| PUB-02 | A | Third party | A relative answers | medium |
| PUB-03 | A | Identity | Three wrong answers | medium |
| PUB-04 | A | Dates | Wants a date after the deadline | medium |
| PUB-05 | A | Hardship | Lost his job, declines the support team | medium |
| PUB-06 | A | Amounts | Full amount is too much, accepts the floor | hard |
| PUB-07 | A | Dates | A vague period, then a holiday | hard |
| PUB-08 | A | Already paid | Says she already paid | medium |
| PUB-09 | A | Restructuring | Asks for a new payment plan | medium |
| PUB-10 | A | Tool failure | The promise service fails on the first call | hard |
| PUB-11 | B | Multi-product | Three products, one date for all | medium |
| PUB-12 | B | Multi-product | Promises one product, refuses two | hard |
| PUB-13 | B | Multi-product | One date for both, but the deadlines differ | hard |
