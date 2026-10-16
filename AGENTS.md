# Repository agent instructions

Task-specific workflows live in [`guides/`](guides/). Read the relevant guide
when its task comes up; the user should not need to repeat these preferences.

| Task | Guide |
|---|---|
| Plan, organize, stage, or create Git commits | [`guides/commits.md`](guides/commits.md) |
| Create, split, organize, or improve tickets | [`guides/ticket_creation.md`](guides/ticket_creation.md) |
| Implement a new task (ticket → TDD tests via subagent → implementation) | [`guides/tdd_workflow.md`](guides/tdd_workflow.md) |

Use [`CONTRIBUTING.md`](CONTRIBUTING.md) for development checks and conventions,
[`tests/README.md`](tests/README.md) for current test commands and intentional
scaffold failures, and [`tickets/README.md`](tickets/README.md) for ticket scope
and dependencies. Inspect their current contents rather than assuming a phase
or ticket is still in the state described by an older conversation.

Explicit user instructions take precedence over these defaults. Approval already
given for the current commit batch remains valid; do not request it again.

Add future workflows as individual files in `guides/` and link them in the table
above. Keep this entry point short and put each workflow's instructions and
examples in its own guide.
