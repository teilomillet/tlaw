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

## Prove core behavior

Run the shared push gate before handing work off or pushing:

```console
./scripts/gate
```

For a changed pure core boundary, also run its independent laws against the
unchanged implementation and mutation-grade those laws. The repository's
current exhaustive set is:

```console
./scripts/core-evidence
```

The fast gate separately requires every retained CrossHair contract to say
`Confirmed over all paths`; a timeout or `Not confirmed` is a failure, not a
weaker kind of pass. The interpretation and limits of each evidence layer are
documented in `docs/core-evidence.md`. Update that document and the shared
script when adding a boundary: evidence that only exists in a local transcript
is not a maintained repository guarantee.

## Prepare a release

The version remains an explicit human choice. A release is published by
`.github/workflows/release.yml`, which only runs automatically when a push to
`main` changes the version in `pyproject.toml`.

Before choosing the version, run the no-upload preflight against the current
tree:

```console
gh workflow run release.yml
```

This runs the same lockfile, changelog-format, gate, build, and installed-wheel
checks the publish path uses, without uploading anything. The publish path
additionally requires the pending fragments to be compiled and a dated changelog
heading for the new version. Then prepare the release, for example 0.0.11:

```console
uv version 0.0.11
uv run towncrier build --yes --version 0.0.11
git diff -- CHANGELOG.md changes pyproject.toml uv.lock
git add pyproject.toml uv.lock CHANGELOG.md changes/
git commit -m "chore(release): publish tlaw 0.0.11"
git push origin main
```

Review the generated changelog before committing it. Towncrier consumes the
fragments, so the release commit includes their deletions. The workflow refuses
to publish when fragments remain or when `CHANGELOG.md` has no dated entry for
the version in `pyproject.toml`. It then reruns `./scripts/gate` before building
any distribution, so publishing cannot succeed while the separate CI gate is
failing.

After a successful PyPI upload the workflow creates the annotated tag
`v<version>` on the release commit and a GitHub Release from that version's
changelog section, with the verified distributions attached. It also uploads
PEP 740 provenance attestations for the published files. The tag and release
are not a second approval step: they are published records of the artifact PyPI
accepted.

A failed workflow does not require a new version. Fix the cause and rerun the
failed run with `gh run rerun <run-id>`, or dispatch the current version
explicitly with `gh workflow run release.yml -f publish=true`. The upload skips
files PyPI already has, and the tag/release step updates an existing release
instead of failing. Bump the version only when the artifact itself must
change; do not amend a version whose files are already on PyPI.
