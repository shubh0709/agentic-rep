# Implementing new tasks: ticket → TDD tests → implementation

## The preferred result

Every new task should follow this fixed order:

1. **Create a ticket** that describes the user-visible outcome, where it fits,
   the files it touches, and how it will be verified. Follow
   [`guides/ticket_creation.md`](ticket_creation.md) and add the new file under
   `tickets/` before any code or test is written.
2. **Write the tests first** by spinning up a subagent with a TDD brief. The
   subagent must produce failing tests that pin down the contract from the
   ticket — both **unit tests** (isolating the component's contract and
   relevant error paths) and **end-to-end tests** (exercising the real
   application path with external services, inference, hardware, and network
   locations explicitly substituted) — then stop without implementing
   production code.
3. **Implement the change** (in the main session or another subagent) until the
   new tests pass and the regression suite stays green. Record verification
   evidence in the ticket.

Do not skip the ticket. Do not start by editing production code. Do not write
production code during step 2. The user should be able to read the ticket and
the failing test diff and understand exactly what the implementation step must
deliver.

## When this workflow applies

Apply it to every new task the user assigns, including:

- New features and capabilities.
- Bug fixes that need a regression test.
- Refactors that change observable behavior.
- New tickets added to a phase backlog.

Small documentation-only edits, one-line typo fixes, and pure commit-history
rearrangements do not need a ticket. Anything that adds, removes, or changes
runtime behavior does.

## Step 1 — Create the ticket

Read [`guides/ticket_creation.md`](ticket_creation.md) and the related guidance
in [`AGENTS.md`](../AGENTS.md), [`tickets/README.md`](../tickets/README.md),
[`tests/README.md`](../tests/README.md), and [`CONTRIBUTING.md`](../CONTRIBUTING.md)
before drafting.

Use the reusable ticket outline from that guide. Pick the next free
`SRA-NNN` number, set the right phase, dependencies, and "Blocks" entries, and
link the new file from the ticket board. Describe the working result, the files
involved, the contracts, and the verification approach. Acceptance criteria
must be observable, not just file-creation.

Creating or explaining a ticket does not authorize implementing it. Wait for the
user's explicit go-ahead before moving to step 2.

## Step 2 — Write the tests via a subagent (TDD)

Dispatch a subagent using the `task` tool with `subagent_type: "general"`. The
subagent's brief must include:

- The new ticket's path and `SRA-NNN` ID.
- The exact contracts from the ticket (signatures, inputs/outputs, error
  types, side effects, cleanup, timing).
- The relevant existing files to read: implementation stubs, fixtures, helpers,
  neighboring tests, and the design or plan sections the ticket links.
- A requirement to cover **both layers**: unit tests under `tests/unit/`
  isolating the new component's contract and error paths, plus end-to-end
  tests under `tests/e2e/` (or the relevant `tests/integration/` location) that
  keep the real application path under test and substitute only the external
  boundary called out by the ticket.
- The test commands from [`tests/README.md`](../tests/README.md) and
  [`CONTRIBUTING.md`](../CONTRIBUTING.md) the subagent should run.
- An explicit instruction to **only write tests and supporting fixtures/stubs**,
  and to leave production code untouched. The tests must fail for the right
  reason (missing behavior) and not because of import or collection errors.

Tell the subagent to return a concise report: the files it added or changed,
the focused command(s) it ran, the observed failure mode (assertion or
`NotImplementedError`), and any assumptions or follow-ups it surfaced. The
main session keeps the diff isolated to tests at this point.

If the subagent also implements production code or weakens tests to make them
pass, reject the result and re-run with a tighter brief. Do not merge a step-2
delivery that contains behavior changes.

## Step 3 — Implement the change

After reviewing the failing tests, implement the change in the main session or
another subagent. The implementation must:

- Make the new tests pass without weakening them.
- Keep the existing regression suite green (default `pytest -q` plus any phase
  markers the ticket depends on, and `ruff` / `pyright` per
  [`CONTRIBUTING.md`](../CONTRIBUTING.md)).
- Match the contracts, files, and diagrams from the ticket.
- Stay inside the ticket's scope. Discoveries that change scope belong in a new
  ticket, not a silent expansion.

Use the focused commands from the ticket's "How to test this ticket alone"
section while iterating, then run the full suite before reporting back.

## Step 4 — Record verification and close the loop

Append a verification record to the ticket (date, environment, commands, new
IDs/artifacts, observed results, failures, cleanup) and update
[`PROGRESS.md`](../PROGRESS.md) plus the ticket board when the acceptance
criteria are met. Cite the actual test output, not a summary of expected
behavior.

## Review before finishing a step

- Step 1: does the ticket explain the result, the files, the contracts, and
  verification, and is it linked from the board?
- Step 2: do the new unit tests and end-to-end tests both fail because the
  behavior is missing, and is the diff free of production changes?
- Step 3: do the new tests pass, does the regression suite stay green, and is
  the implementation inside the ticket's scope?
- Step 4: is the verification evidence recorded with real commands and
  observed outputs?

## Examples to follow

- Existing tickets in [`tickets/`](../tickets/) demonstrate the structure
  produced in step 1.
- The Phase 2 and Phase 3 sections of
  [`tests/README.md`](../tests/README.md) describe the red → green → refactor
  commands the subagent should run in step 2 and that step 3 must keep green.
- The "What each layer proves" and "Ticket commands" tables in
  [`tests/README.md`](../tests/README.md) show how unit and end-to-end coverage
  is paired for each ticket; the subagent should mirror that split.