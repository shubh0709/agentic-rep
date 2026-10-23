# Implementing new tasks: ticket → TDD tests → implementation

*Owns the implementation workflow and its ordering. The ticket outline lives in [`guides/ticket_creation.md`](ticket_creation.md); the test commands and markers live in [`tests/README.md`](../tests/README.md); lint, type checking, and the migration workflow live in [`CONTRIBUTING.md`](../CONTRIBUTING.md).*

## The preferred result

Every new task follows this fixed order:

1. **Create a ticket** describing the user-visible outcome, where it fits, the files it touches, and how it will be verified. Follow [`guides/ticket_creation.md`](ticket_creation.md) and add the file under `tickets/` before any code or test is written.
2. **Write the tests first** by spinning up a subagent with a TDD brief. The subagent produces failing tests that pin the contract from the ticket — both **unit tests** (isolating the component's contract and relevant error paths) and **end-to-end tests** (exercising the real application path with external services, inference, hardware, and network locations explicitly substituted) — then stops without implementing production code.
3. **Implement the change** (in the main session or another subagent) until the new tests pass and the regression suite stays green. Record verification evidence in the ticket.

Do not skip the ticket. Do not start by editing production code. Do not write production code during step 2. The user should be able to read the ticket and the failing test diff and understand exactly what the implementation step must deliver.

## When this workflow applies

Apply it to every new task the user assigns: new features, bug fixes that need a regression test, refactors that change observable behavior, and new tickets added to a phase backlog.

Small documentation-only edits, one-line typo fixes, and pure commit-history rearrangements do not need a ticket. Anything that adds, removes, or changes runtime behavior does.

## Step 1 — Create the ticket

Read [`guides/ticket_creation.md`](ticket_creation.md) and its [reusable ticket outline](ticket_creation.md#reusable-ticket-outline), plus [`tickets/README.md`](../tickets/README.md) and [`PROGRESS.md`](../PROGRESS.md), before drafting.

Pick the next free `SRA-NNN` number, set the right phase, dependencies, and "Blocks" entries, and link the new file from the ticket board. Describe the working result, the files involved, the contracts, and the verification approach. Acceptance criteria must be observable, not just file-creation.

Creating or explaining a ticket does not authorize implementing it. Wait for the user's explicit go-ahead before moving to step 2.

## Step 2 — Write the tests via a subagent (TDD)

Dispatch a subagent using the `task` tool with `subagent_type: "general"`. The brief must include:

- The new ticket's path and `SRA-NNN` ID.
- The exact contracts from the ticket (signatures, inputs/outputs, error types, side effects, cleanup, timing).
- The relevant existing files to read: implementation stubs, fixtures, helpers, neighboring tests, and the design sections the ticket links.
- A requirement to cover **both layers**: unit tests under `tests/unit/` isolating the new component's contract and error paths, plus end-to-end tests under `tests/e2e/` (or the relevant `tests/integration/` location) that keep the real application path under test and substitute only the external boundary the ticket calls out. [`tests/README.md`](../tests/README.md#what-each-layer-proves) explains the split.
- The focused commands from [`tests/README.md`](../tests/README.md#ticket-commands) the subagent should run.
- An explicit instruction to **only write tests and supporting fixtures/stubs**, leaving production code untouched. The tests must fail for the right reason (missing behavior), not because of import or collection errors.

Tell the subagent to return a concise report: the files it added or changed, the focused command(s) it ran, the observed failure mode (assertion or `NotImplementedError`), and any assumptions or follow-ups it surfaced. The main session keeps the diff isolated to tests at this point.

If the subagent also implements production code or weakens tests to make them pass, reject the result and re-run with a tighter brief. Do not merge a step-2 delivery that contains behavior changes.

## Step 3 — Implement the change

After reviewing the failing tests, implement the change in the main session or another subagent. The implementation must:

- Make the new tests pass without weakening them.
- Keep the existing regression suite green (the default `pytest -q` plus any phase markers the ticket depends on, and `ruff` / `pyright` per [`CONTRIBUTING.md`](../CONTRIBUTING.md)).
- Match the contracts, files, and diagrams from the ticket.
- Stay inside the ticket's scope. Discoveries that change scope belong in a new ticket, not a silent expansion.

Use the ticket's focused commands while iterating, then run the full suite before reporting back.

## Step 4 — Record verification and close the loop

Append a verification record to the ticket (date, environment, commands, new IDs/artifacts, observed results, failures, cleanup) following the [manual-evidence rule](../tests/README.md#the-manual-evidence-rule), and update [`PROGRESS.md`](../PROGRESS.md) plus the ticket board when the acceptance criteria are met. Cite the actual test output, not a summary of expected behavior.

## Review before finishing a step

- Step 1: does the ticket explain the result, the files, the contracts, and verification, and is it linked from the board?
- Step 2: do both the new unit and end-to-end tests fail because the behavior is missing, and is the diff free of production changes?
- Step 3: do the new tests pass, does the regression suite stay green, and is the implementation inside the ticket's scope?
- Step 4: is the verification evidence recorded with real commands and observed outputs?

## Examples to follow

- Existing tickets under [`tickets/`](../tickets/) demonstrate the structure produced in step 1; [SRA-201](../tickets/SRA-201-text-capture-api-client.md) is the fullest worked example.
- [`tests/README.md`](../tests/README.md) pairs unit and end-to-end coverage per ticket; mirror that split in step 2 and keep it green in step 3.