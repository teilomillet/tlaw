# Repository instructions

Always make sound, epistemic decisions and justify them with direct evidence.
Do not assume repository behavior: inspect it and run the relevant checks.

## Changes and changelog

Read `CONTRIBUTING.md` before changing code or preparing a commit.

- A user-visible API, CLI, output, error, behavior, packaging, or supported
  input change must add one `changes/+short-name.<category>.md` file in the
  same commit.
- Write the fragment for users: one clear sentence, present tense, without a
  bullet prefix or implementation diary.
- Valid categories and examples live in `changes/README.md`.
- Do not edit `CHANGELOG.md` during development. It is generated from the
  fragments when preparing a release.
- An internal-only implementation change may omit a fragment, but its commit
  message must include a `Changelog: skip` footer.

## Commits

- Commit only intentional files. Preserve unrelated and concurrent work.
- Use `type(scope): imperative summary`, with an optional scope and a first
  line no longer than 72 characters.
- Allowed types are `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`,
  `ci`, `chore`, and `revert`.
- Before committing, inspect the staged diff. For a user-visible change, run
  `uv run towncrier check --staged`. Always run `uv run ruff check .` and the
  relevant tests.
- Never generate or accept a commit message that claims a fragment exists
  unless that fragment is staged.
- Before handing work off as ready or pushing it, run `./scripts/gate`.

## Releases

Follow the release sequence in `CONTRIBUTING.md`. A release commit changes the
version, compiles and reviews the fragments, commits the resulting changelog
and fragment deletions, and only then pushes to `main`.
