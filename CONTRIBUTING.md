# Contributing to tlaw

The changelog is a user-facing behavioral record, not a dump of commit titles.
Humans and agents write small change fragments while developing; Towncrier
deterministically compiles them into `CHANGELOG.md` for a release.

## Describe a change

For every user-visible API, CLI, output, error, behavior, packaging, or
supported-input change, add a fragment in the same commit:

```text
changes/+integer-domain.added.md
```

The file contains one sentence without a bullet prefix:

```text
Add `tlaw.integer(low, high)` for bounded integer inputs.
```

Use the categories and naming rules in `changes/README.md`. Do not edit
`CHANGELOG.md` during ordinary development.

An internal refactor, test-only change, or maintenance change may omit a
fragment. Make that decision explicit by adding this footer to the commit
message:

```text
Changelog: skip
```

Preview the pending release notes at any time without changing files:

```console
uv run towncrier build --draft
```

Before committing a user-visible change, stage the intended files and check
the fragment:

```console
uv run towncrier check --staged
uv run ruff check .
uv run pytest -q
```

## Write the commit message

Use a Conventional Commit-shaped first line:

```text
type(scope): imperative summary
```

The scope is optional. Keep the first line at 72 characters or fewer. Allowed
types are `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`,
`chore`, and `revert`.

Examples:

```text
feat(domains): add bounded integer inputs
fix(check): retain the wrong exception in refusal failures
docs(release): explain change fragments
```

Zed reads the repository's `AGENTS.md`, and `.zed/settings.json` gives its
commit-message generator the same rules. For command-line Git, enable the
tracked reminder template and hooks once per clone:

```console
git config commit.template .gitmessage
git config core.hooksPath .githooks
```

The `commit-msg` hook enforces the title shape and requires either a staged
change fragment or the exact `Changelog: skip` footer. A
`chore(release): ...` commit with a staged generated changelog is the release
exception. The `pre-push` hook runs the same `scripts/gate` command as CI.

## Prepare a release

The version remains an explicit human choice. For example, to release 0.0.3:

```console
uv version 0.0.3
uv run towncrier build --yes --version 0.0.3
git diff -- CHANGELOG.md changes pyproject.toml uv.lock
git add pyproject.toml uv.lock CHANGELOG.md changes/
git commit -m "chore(release): publish tlaw 0.0.3"
git push origin main
```

Review the generated changelog before committing it. Towncrier consumes the
fragments, so the release commit includes their deletions. The PyPI workflow
refuses to publish when fragments remain or when `CHANGELOG.md` has no entry
for the version in `pyproject.toml`.
