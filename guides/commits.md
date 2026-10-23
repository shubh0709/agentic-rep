# Creating commits in this repository

*Owns the commit workflow: batch inspection, staging and approval rules, message conventions, and the approved worked example. The check commands live in [`CONTRIBUTING.md`](../CONTRIBUTING.md) and [`tests/README.md`](../tests/README.md); the ticket outline lives in [`guides/ticket_creation.md`](ticket_creation.md).*

## The preferred result

Create a readable history of small, logical steps: project foundations where needed, dependencies and tooling, a test scaffold when relevant, then feature work grouped by ticket in dependency order. A reviewer should understand what each commit adds and why it belongs together. The user approved the 12-commit Phase 1 sequence below as the model for this style. **Twelve is an example, not a required count.** An ordinary fix or docs change may need only one commit. Do not repeat initial setup commits in an established repository or turn every file into a separate commit.

## Inspect and propose the batch

1. Read the current branch/history, `git status --short`, staged and unstaged diffs, and relevant untracked files. Staging may contain an older version of a file than the working tree; include the intended latest changes.
2. Read the relevant tickets, their dependencies, and `PROGRESS.md`. Group changes by behavior or purpose, including related tests and documentation. Split shared files into relevant hunks when necessary.
3. For a new batch, present the **commit count, exact ordered messages, and breakdown of files or behavior in each commit before staging or committing**. Identify any intentionally failing test baseline and any work excluded from the batch. Wait for approval unless the user has already approved the batch or explicitly authorized proceeding without a proposal.
4. Once authorized, carry out that batch without asking for permission for each commit. If newer, unrelated work appears while preparing it, preserve that work and leave it uncommitted; explain what remains afterward.

Do not reinterpret approval for one batch as permission to commit future work, push to a remote, or rewrite existing shared history. Do not invent an author: use the configured Git identity, asking for a name/email only if it is missing.

## Grouping and messages

Use this message shape:

```text
type(optional-scope): concrete action (SRA-NNN)
```

Keep the **subject line to 50 characters or fewer**. Tooling such as GitHub and GitHub Desktop warn at 70–72 characters, so any subject at that length should be shortened. **Wrap commit body text at 72 characters per line.** These limits do not rewrite history, but every new commit must follow them.

Use `chore` for setup/tooling, `build` for dependencies, `feat` for behavior, `fix` for corrections, `test` for test-focused changes, and `docs` for documentation. Omit the ticket suffix when there is no applicable ticket, and prefer a short imperative description over messages such as `updates`, `misc changes`, or `finish stuff`.

- Put prerequisite work before the features that depend on it.
- Default to one cohesive commit per implementation ticket. Split a large ticket when it contains independently reviewable steps; avoid artificial splits for a small ticket.
- Keep dependencies and their corresponding lockfile changes together.
- Keep model changes with migrations, and behavior changes with their tests and relevant documentation, unless using an explicitly agreed TDD sequence.
- Give cross-cutting foundations, such as optional tracing, their own commit when useful. Add route integration with the feature that consumes them.
- Update ticket/progress documentation with the relevant work or in a clearly described phase-verification commit. Do not claim a ticket is complete until its acceptance criteria and verification support that claim.

### Approved Phase 1 example

This is the actual initial-history breakdown accepted by the user. Adapt its structure to the current work rather than copying its count or old ticket scope.

| # | Message | Breakdown |
|---|---|---|
| 1 | `chore: initialize project setup` | Packaging, base dependencies, package structure, ignores, license, README. |
| 2 | `chore(dev): add development tooling and CI` | Ruff, Pyright, pre-commit, CI, editor settings, contribution guidance, GitHub templates. |
| 3 | `build(ai): add LangChain and OpenAI stack` | AI dependencies, structured schemas, model factories and interfaces. |
| 4 | `feat(observability): add LangSmith tracing` | Tracing dependency, settings, helper, environment examples, setup docs. |
| 5 | `test: add Phase 1 TDD scaffold and tests` | Fixtures, mocked models, supporting models and stubs, baseline tests, backlog. |
| 6 | `feat(config): implement cached settings (SRA-101)` | Cached environment settings and validation. |
| 7 | `feat(scheduler): implement SR scheduling (SRA-102)` | Calendar-aware intervals and review persistence. |
| 8 | `feat(db): add user repo and migrations (SRA-103)` | Entry storage, search, review operations, ownership checks, migrations. |
| 9 | `feat(api): add digest and search routes (SRA-104)` | API wiring, local-user dependency, digest/search routes. |
| 10 | `feat(ingestion): add transcript agent (SRA-105)` | Extraction, validation, persistence, error handling, route integration, tests. |
| 11 | `feat(query): implement study chat agent (SRA-106)` | Study tools, agent loop, chat integration, tests. |
| 12 | `test(integration): verify Phase 1 loop (SRA-107)` | End-to-end and tracing verification, due-date assertions, phase docs. |

A smaller batch could be proposed like this: `build(capture): add local transcription dependency` (dependency declaration and lockfile changes), then `feat(capture): add Whisper transcription (SRA-202)` (implementation, focused tests, ticket documentation). These are planning examples, not claims that the listed work is currently pending.

## TDD and teaching scaffolds

This repository intentionally uses teaching stubs and acceptance tests. An approved scaffold commit can be red: tests should collect successfully and fail because the targeted behavior is unimplemented, not because dependencies or imports are broken. State that intention in the commit body and proposal.

Subsequent ticket commits should make the corresponding tests pass while keeping completed-ticket coverage green. Follow [`tests/README.md`](../tests/README.md) for the remaining scaffold failures. Do not hide them with new skips, `xfail`, or assertions that merely expect `NotImplementedError`. Phase integration requires the checks specified by its ticket to pass.

For ordinary ongoing work, commit the real implementation and its tests. Only reconstruct earlier scaffold states when the user has agreed to organize an uncommitted backlog that way, as they did for the initial Phase 1 history. Make clear that this is a logical organization of existing work, not evidence of its original development chronology, and preserve the final implementation.

## Secrets and preserving work

Never commit real credentials, local `.env` files, private keys, local databases, virtual environments, or temporary preparation artifacts. Environment examples must contain placeholders; test credentials must be clearly fake.

Inspect the exact staged content for credential patterns and sensitive paths before each commit, and use an available secret scanner when practical. For a reconstructed sequence, inspect all newly introduced file versions across the batch, not just the final tree: a credential deleted later still exists in history. Do not print secret values in output or include them in messages; report only redacted findings and file locations. `.gitignore` alone is not a secret check, and adding an ignore rule does not untrack an existing file.

Prefer explicit paths or hunk staging. Avoid sweeping unrelated work into `git add .`. Preserve pre-existing edits and staging outside the approved scope. An isolated checkout can help with complex splitting, but compare its tracked path list against the intended files as well as comparing contents and file modes. In particular, a directory ignore such as `.venv/` does not exclude a symlink named `.venv`; never let a temporary environment link enter a commit.

Do not reset working files or recreate implemented stubs in the user's working tree just to arrange history. Do not amend or reorder existing commits unless that is part of the authorized task.

## Verify and report

Run the checks appropriate to each change using [`CONTRIBUTING.md`](../CONTRIBUTING.md) and [`tests/README.md`](../tests/README.md). Respect installed commit hooks; resolve failures rather than bypassing them. Docs-only changes usually need link/content review rather than an application test run. Do not reuse historical pass counts as current results.

Before finishing, confirm the count, order, messages and scope match the approved batch; that no temporary files, credentials, or unrelated changes entered any new commit; that the final committed content matches the intended implementation while newer or excluded work remains intact (explaining any intentional differences); and that relevant checks passed, with any expected scaffold failures stated explicitly.

Report the number of commits, branch, useful hashes, checks run, secret-scan result, and any remaining uncommitted work. Never describe the working tree as clean when excluded changes remain. Push only when separately authorized.