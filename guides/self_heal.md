---
name: application-self-heal
description: Discover, reproduce, track, and fix application bugs using repeatable API tests and regression tests, with verified commits per ticket.
---

# Application Self-Heal

*Owns the nine-step bug-discovery and repair process. Test commands live in [`tests/README.md`](../tests/README.md); commit rules in [`guides/commits.md`](commits.md); ticket format in [`guides/ticket_creation.md`](ticket_creation.md). Run results, coverage counts, and per-bug evidence belong in [`tickets/self_heal/SH-REPORT.md`](../tickets/self_heal/SH-REPORT.md).*

## Goal

Systematically discover and repair application bugs with evidence. Deliver a repeatable Postman collection, a coverage inventory, bug tickets, regression tests, verified fixes, and a completion report.

## Operating rules

- Read repository instructions and preserve unrelated user changes.
- Use a local or dedicated test environment with isolated test data.
- Keep credentials in environment variables and out of reports or commits.
- Derive expected behavior from documented contracts, requirements, and established business rules.
- Treat existing code as evidence of implementation, not proof of correctness.
- If expected behavior is ambiguous, record the ambiguity and continue with independent work.
- Never weaken assertions, skip failing tests, or change requirements solely to make a run pass.
- Do not claim the entire application is tested unless the coverage inventory supports that claim.
- Commit verified changes locally. Publishing or deployment is outside this workflow unless separately requested.

## 1. Inspect the application and establish a baseline

Identify startup and test commands (see [`tests/README.md`](../tests/README.md)), API routes, schemas, authentication methods, roles, and tenants, important business flows and state transitions, database writes, queues, background jobs, and external integrations, plus existing tests, API documentation, and known issues.

Run existing relevant checks before making changes. Record pre-existing failures and environment limitations in the report.

Create a coverage matrix containing:

| Feature / endpoint | Scenario | Expected behavior source | Test ID | Status |
|---|---|---|---|---|

Distinguish passed, failed, blocked, and untested scenarios.

## 2. Build a repeatable test harness

Create a Postman collection, environment template, test datasets, and a documented command for automated execution. Use the repository's existing runner where appropriate.

The harness must:

- Obtain authentication through the supported flow.
- Create uniquely identifiable test fixtures.
- Capture generated IDs rather than hardcoding them.
- Run dependent requests in a defined order.
- Support isolated reruns of individual failing scenarios.
- Clean up its own data, including after failures.
- Produce machine-readable results and a readable summary.
- Return a failing exit status when assertions fail.
- Record enough sanitized evidence to reproduce failures.

Cover applicable scenarios:

- Happy paths and complete business workflows.
- Missing, malformed, empty, and boundary inputs.
- Missing, expired, and invalid authentication.
- Role permissions and cross-user / cross-tenant access.
- Missing resources and invalid state transitions.
- Pagination, filtering, and sorting.
- Duplicate requests and idempotency.
- Dependency errors and recovery behavior.

Use appropriate supplementary tests for UI behavior, concurrency, background jobs, and other behavior Postman cannot adequately verify. Record anything that remains outside coverage.

## 3. Assert meaningful outcomes

For each scenario, verify the exact expected status code, response schema and meaningful field values, relevant headers and error contracts, persisted state through read-back or suitable integration tests, intended side effects and absence of unintended side effects, and business invariants such as no duplicate charge or oversold stock.

For asynchronous behavior, poll for a defined condition with a bounded timeout. Avoid arbitrary sleeps. Do not infer correctness solely from a successful HTTP response.

## 4. Execute and classify failures

Run the suite and preserve the results. For each failure:

1. Reproduce it with the smallest reliable scenario.
2. Inspect relevant logs, code, state, and dependency behavior.
3. Classify it as a confirmed application bug, a test or expectation defect, a fixture or environment issue, or unresolved / requiring clarification.
4. Record the evidence supporting that classification.

Do not modify application code to compensate for an incorrect test. Do not hide intermittent failures through silent retries.

## 5. Create and prioritize tickets

Use the requested issue tracker when available. Otherwise, create local Markdown tickets with stable IDs, following the format in [`guides/ticket_creation.md`](ticket_creation.md).

Each ticket must include an ID and concise title, severity and user impact, affected feature and baseline revision, preconditions and minimal reproduction steps, expected behavior and its source, actual behavior and sanitized evidence, suspected root cause (clearly labeled if unconfirmed), acceptance criteria, dependencies and associated test IDs, and status.

Group failures caused by the same defect. Prioritize security, data integrity, and broken critical flows.

## 6. Repair each confirmed bug

For each ticket:

1. Add a focused regression test at the appropriate layer.
2. Run it against the unfixed code.
3. Confirm it fails because of the reported defect.
4. Implement the smallest complete fix for the root cause.
5. Confirm the regression test now passes.
6. Rerun the original failing API scenario.
7. Run related tests and required repository checks.
8. Review the diff for regressions and unrelated changes.

Use unit tests for isolated logic, integration tests for component interactions, and end-to-end tests for important user workflows. Do not add redundant tests at every layer without a reason.

Parallelize only when tickets have independent changes and isolated test state. Use separate branches or worktrees when needed, and integrate and verify the combined result before declaring completion.

## 7. Commit verified fixes

Create one logical commit per ticket where practical, following [`guides/commits.md`](commits.md). For example: `fix(BUG-012): prevent duplicate reservation on request retry`. Include the fix and its regression tests, and stage only intended files.

Record the commit hash and validation evidence in the ticket or report. Keep tickets open or blocked if their acceptance criteria are unmet.

## 8. Verify the complete result

After integrating fixes, rerun the complete API collection, run the required repository checks and relevant broader regression tests, confirm fixtures and cleanup work on a fresh run, review outstanding failures and coverage gaps, and confirm combined changes preserve business invariants.

Passing individual ticket tests is insufficient if the integrated application still fails.

## 9. Stop and report honestly

Continue until all confirmed in-scope tickets are verified or explicitly blocked, and the final verification run is complete. Default to a maximum of three repair attempts per ticket.

If a defect remains unresolved, record attempted fixes, evidence, and the next required action, then continue with independent tickets.

Report coverage achieved and remaining gaps, the passed/failed/blocked/untested scenarios, tickets fixed with their commit hashes, remaining issues and blockers, and the exact commands needed to reproduce the verification run. Write that report to [`tickets/self_heal/SH-REPORT.md`](../tickets/self_heal/SH-REPORT.md).

Never claim the application is bug-free. State what was tested and what the evidence demonstrates.