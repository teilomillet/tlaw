# Changelog

All notable user-visible changes are recorded here. Changes for an upcoming
release are authored as small files in `changes/`; Towncrier compiles those
files into this document when the release is prepared.

<!-- towncrier release notes start -->

## 0.0.3 — 2026-07-23

### Added

- Add `tlaw diff BASE..HEAD` and a dedicated CI review summary to report static
  law-space changes, including generation and decorators, without importing or
  executing either Git revision.
- Add folder discovery to `tlaw check`: pointing it at a directory runs every
  `laws.py` and `*_laws.py` beneath it and passes only when every file passes;
  per-file evidence flags are refused in folder mode rather than silently
  ignored.
- Add seeded randomized search, stable retained check reports, and exact
  counterexample replay with `tlaw check --search`, `--json`, and `--replay`.
- Expand aliased domains in `--explain`: a PEP 695 alias such as `list[Reward]`
  for `Annotated[float, tlaw.number(0, 1)]` is now shown as `list[number(0,
  1)]`, so the generated domain is visible instead of hidden behind the alias
  name.
- Support namespace packages (PEP 420) as mutation boundaries: tlaw reads the
  dotted module name statically from the laws, then copies and shadows the top
  namespace directory, including a regular child package beneath a namespace
  parent.
- Verify the `Annotated` base type against its tlaw domain: a base that can
  only be a type the domain never produces (`Annotated[str, tlaw.number(0,
  1)]`) is refused at decoration time, while a permissive or unprovable base is
  left alone, so the visible contract cannot claim a type the domain does not
  generate.

### Fixed

- Report a package mutant's rejecting law as a portable
  `laws.py::function_name` identity instead of exposing a temporary-workspace
  path.


## 0.0.2 — 2026-07-23

### Added

- Add `scripts/gate`, run by both the `.githooks/pre-push` hook and CI so they
  share one command list; it verifies the dependency lock, runs ruff and the
  property suite, and uses `tlaw check` plus `tlaw mutate` on the flat
  examples.
- Add `tlaw check --explain` output for generated domains, example budgets, the
  Hypothesis version, and deterministic posture.
- Add `tlaw.depends(builder)` dependent domains, so one parameter's domain can
  be derived from another's generated value; relations such as "no sequence
  exceeds the budget" are satisfied by construction rather than narrowed inside
  the law body, and `--explain` reports the dependency instead of a wider
  domain.
- Add `tlaw.one_of(values)` for domains that are a finite computed set rather
  than a range, such as the divisors of a drawn GPU count; it composes with
  `depends` and refuses an empty collection at definition time.
- Add `tlaw.raises(Expected, function, *arguments)` exception contracts whose
  failures name a returned value or the wrong exception instead of reporting a
  bare `False`.
- Add `tlaw.same(left, right, within=...)` with absolute tolerance and
  structural comparison for numbers, strings, sequences, dictionaries, and
  array-like values, retaining the first difference for failures.
- Add a `relative=` tolerance to `tlaw.same`, combining with the absolute
  `within=` like `math.isclose`, so laws over magnitude-spanning numerical
  values (comparing a refactor to its reference) can state correctness to
  relative precision instead of a fixed absolute gap.
- Add a bare `@tlaw.law` form that turns annotated functions into deterministic
  Hypothesis laws, uses function names as descriptions, and reports minimized
  failing inputs.
- Add a complete law-first batch-packer example that records red laws before
  implementation and kills all 13 generated mutants afterward.
- Add a law-first score-weights example with a real surviving mutant documented
  in an equivalence ledger.
- Add an always-on canary that rejects invalid mutation evidence when the laws
  do not observe a poisoned implementation.
- Add deterministic generated mutation evidence with content-based mutation
  IDs, calibrated timeouts, replay-verified kills, equivalence ledgers, and
  stable `tlaw.mutation/v1` JSON reports.
- Add visible `number`, `integer`, and `list_of` input domains, including
  supported `Annotated` and PEP 695 alias forms, while rejecting unsupported
  positions and metadata instead of silently ignoring them.
- Pin the command-line exit contract with tests at the argparse boundary: `0`
  when the evidence holds, `1` for a law failure or unexplained survivor, and
  `2` for broken evidence such as a surviving canary.
- Pin the public surface: every name in `tlaw.__all__` must be importable, and
  the authoring vocabulary must stay callable, so a feature cannot be
  half-wired into the package.
- Reject a passing bare law when it mutated one of its generated inputs.
- Support package-shaped mutation boundaries: `tlaw mutate laws.py
  src/pkg/module.py` copies the whole package and shadows the installed one, so
  intra-package imports resolve inside the mutated copy, the laws may live
  anywhere, and reports name the boundary shape and dotted module.

### Changed

- Disable automatic loading of third-party pytest plugins in every tlaw runner.
- Make `--explain` state that explicit laws use ordinary Hypothesis settings
  and do not receive the bare form's input-mutation guard.

### Known limitations

- Mutation boundaries cover flat directories and regular packages; namespace
  packages without `__init__.py`, laws that must mutate two packages at once,
  and compiled modules are not supported yet.


## 0.0.1 — initial scaffold

- Package scaffold, `tlaw check`, `tlaw audit` with declared known-wrong
  implementations, stateful path API (`system`, `call`, `then`,
  `same_as`), and the categorical path core.
