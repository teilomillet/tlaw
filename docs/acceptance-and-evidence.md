# Acceptance and evidence

Status: working design detail. The implemented inventory below was re-checked
against the repository on 2026-10-23; sections that state a design target say
so explicitly.

Implemented now: deterministic inferred generation, minimized failures in check
output, third-party pytest plugin isolation, `tlaw check --explain`; visible
`number`/`integer`/`one_of`/`list_of`/`recursive`/`record`/`depends` domains,
including dictionary-form records and finite recursive values; retained `same`
comparisons with absolute and relative tolerance; `raises`, `returns`, and
`excludes` claim objects, with `.and_then(...)` available on `same`, `raises`,
`returns`, and `excludes`; declared audits;
generated mutation evidence for flat modules, regular packages, and namespace
packages, with opt-in exhaustive mutation, campaign planning (`--plan`,
`--max-runs`), and exact mutant-ID reruns (`--mutant`); opt-in exhaustive
checking of supported finite domains with per-law obligation accounting; one
shared local and CI gate; and a version-triggered release pipeline that gates,
builds, verifies the exact wheel, publishes to PyPI with trusted publishing and
PEP 740 provenance attestations, then tags the release and creates a GitHub
Release. Static
`tlaw diff`, randomized `--search`, stable `tlaw.check/v1` reports, repeatable
`--seed`, and exact `--replay` are also implemented and probed.

Opt-in `--exhaustive` checking now enumerates supported small finite domains.
Each law obligation distinguishes sampled, exhaustive, and replay evidence;
only a fully passing enumeration reports exhaustion. See
[finite checking](current-behavior.md#exhaustive-finite-checking) for supported
declarations, per-law limits, and the boundary of that claim.

Checks and replays also account for each selected, collected law case through
setup, call, and teardown. Incomplete execution prevents success while retained
refutations remain visible. See [law obligations](current-behavior.md#law-obligations)
for the implemented statuses, report fields, and exit codes.

## The rule both parts follow

tlaw's position is that a committed law is an accepted law: git is the
acceptance ledger, the commit is the acceptance event, and no separate
registry may compete with `laws.py` as a source of truth.

An uncommitted or proposed change has no separate tlaw status; it is simply a
Git diff. Repository review, branch protection, and CI decide which commit
becomes the shared truth.

That rule is sound only while two claims hold:

1. a change to a law's meaning cannot be overlooked at review time; and
2. a green run never claims more than it measured, and a red run can be
   reproduced by someone who was not there.

Both claims exist for the same reason the laws themselves do: no step of the
trust chain may depend on someone's — or some model's — interpretation.

## Part 1: law changes must be loud

### The attack this defends against

In a repository where agents also commit, Git defines which law the project has
accepted, while repository permissions define who may create that accepted
history. An agent asked to make a check pass has two moves: fix the
implementation, or weaken the law. Both are ordinary changes. The realistic
weakening is not deleting a law; it is narrowing a generated domain, adding an
assumption, loosening a tolerance, or shrinking an observation, buried in a
large diff.

Weakening must remain possible — intent legitimately changes. It must not
remain silent.

### `tlaw diff`

`tlaw diff BASE..HEAD` reports what changed in law space between two git
revisions. It is a derived view over git, never a competing source of truth,
and it makes no judgment about whether a change is good.

A law's identity is its module path plus function name. A law's meaning is:

- its description (the function name);
- its input domain (annotations or explicit domains); and
- its generation posture and enforcement decorators;
- its comparison (the function body).

The report classifies each law as one of:

- **added** or **removed**;
- **domain narrowed**, **domain widened**, or **domain changed** — bounds are
  compared only when both sides are literal and comparable; anything else is
  reported as changed without classification, never guessed; or
- **generation changed** or **decorators changed** — the way inputs are
  generated or the way the law is collected changed;
- **body changed** — the comparison itself differs.

Formatting-only edits are not meaning changes; the comparison works on parsed
structure, not text.

### Reading without executing

`tlaw diff` parses law files from both revisions as source. It never imports
or executes code from a checked-out historical revision. A tool whose job is
review safety must not run the code under review.

The implementation reads blobs with `git cat-file`, recognizes decorators
imported from `tlaw` or `tlaw.authoring`, and compares parsed ASTs. Simple
module-level domain aliases are expanded. Literal numeric, finite-choice, and
list domains are classified only when subset/superset is provable; dependent
or dynamic expressions are `domain changed`. Helpers and module globals are
not chased; their changes remain visible in the ordinary Git diff, and a
readable law should keep its contract meaning in its own body.

The real-history probe now exists. Running
`tlaw diff 62ea585..a482e8d` over tlaw's seven committed feature/release
commits reported 24 added laws across the worked examples and no
implementation files. Separate temporary-repository probes pin literal
narrowing/widening, conservative dynamic-domain changes, formatting immunity,
generation-posture changes, skip decorators, combined law/implementation
commits, and a top-level historical side effect that must never execute.

### CI wiring

- Exit codes distinguish "no law changes" from "law changes present" so a
  pipeline can surface the report as its own check. Whether law changes block
  a merge is repository policy, not tlaw's.
- A commit may change laws and implementation together. The report separates
  the law-space diff so that a behavioral change cannot disappear inside the
  implementation diff; repository policy decides whether separation into
  multiple commits is useful.
- A law change is the trigger for Part 2's audit: when meaning changes,
  strength must be re-established, not assumed.

### What this deliberately does not do

- It does not block weakening. Visibility, not enforcement.
- It does not infer intent or summarize in prose. It reports classified,
  checkable differences.
- It does not add an acceptance ceremony. Branch protection plus human review
  remains the only gate, and it keeps working when the commit author is an
  agent.

## Part 2: green must not overclaim

### Verdict vocabulary

This subsection is a design target, not current `tlaw check` output. Today,
`check` reports per-case law obligations for one selected law file, or lists the
verdict for every law file discovered under a selected folder. tlaw does not
yet enumerate code with no laws and therefore cannot report project-wide
`unknown` behavior.

Every check run reports exactly three kinds of statement:

- **passed** — this law was attempted and not refuted;
- **failed** — this law was refuted by a retained counterexample; and
- **unknown** — this code is in scope but has no law; tlaw makes no claim.

A green run states its own scope: which laws ran, over which declared
domains, with how many examples. It never converts unknown into a percentage
or a score. The moment unknown becomes a metric, it will be gamed, and green
will again mean more than it measured.

### Determinism by default

- Third-party pytest plugin autoload is disabled unless a law opts in. A
  check must not execute unrelated installed software. An opt-in mechanism is
  not implemented yet.
- Example generation is deterministic by default: the same laws against the
  same code produce the same verdicts.
- Annotation metadata that tlaw cannot enforce is rejected instead of being
  silently erased during strategy inference.
- `tlaw check --search` is the explicit randomized mode for finding new
  counterexamples. It records its seed in the report so a discovery is a
  repeatable fact, not an anecdote.

### Replay

A failure report retains everything needed to reproduce it elsewhere:

- the law's name and source location;
- the generation mode and seed;
- the shrunk counterexample inputs; and
- both compared values when the comparison is owned by tlaw — this is the
  same requirement as failure-explanation bottleneck 3, one mechanism serving
  both.

`tlaw check --replay REPORT` reruns exactly the recorded failures. A
counterexample that cannot be replayed on another machine does not count as
evidence.

The implemented `tlaw.check/v1` report stores typed generated inputs and an
exception type/message fingerprint. Hypothesis marks its final rerun of each
minimal failure; tlaw freezes that rerun's inputs before calling the law,
captures only those reruns, and keeps distinct failure origins from one law.
Replay collects only recorded law identities and succeeds only when every
exact input raises the same failure. Unsupported input types, missing law
identities, or malformed reports are invalid evidence, not partial success.

The replay probes retain a seed-shrunk `value=10`, reproduce it from a second
checkout path, keep two distinct exception origins from one law, replay an
input-mutation failure from the original non-empty list, ignore an unrelated
failing pytest test, and turn a missing retained law or unsupported `complex`
input into invalid evidence.

### Implemented layers of falsification

1. `tlaw check` attempts to falsify the implementation against the laws.
2. `tlaw audit` attempts to falsify the claim that the laws constrain
   anything by requiring them to reject author-declared wrong implementations.
3. `tlaw mutate` challenges the same laws with deterministic single-site
   changes generated from one implementation module, so the law author did not
   choose every opponent.

Green on layer 1 alone means "attempted and not refuted." A declared audit is
self-graded evidence and says only that its named wrong set was rejected. A
mutation report says only how the runnable generated mutations were classified;
it never turns that count into correctness or coverage.

### Current usable CI commands

This repository runs `./scripts/gate` locally before a push and in the tracked
GitHub workflow. A separate `Review law changes` job writes `tlaw diff` output
to its own job summary and emits a visible warning when changes exist; a
change is reviewable rather than automatically forbidden. Other repositories
can use the same explicit commands:

- every push: `tlaw check path/to/laws.py --explain`;
- every review: `tlaw diff origin/main..HEAD`;
- scheduled discovery:
  `tlaw check path/to/laws.py --search --json failure.json`;
- reproduce a discovery: `tlaw check --replay failure.json`;
- when declared audit evidence matters: `tlaw audit path/to/audit.py`; and
- when one flat, regular-package, or namespace-package implementation boundary
  matters:
  `tlaw mutate path/to/laws.py path/to/implementation.py`.

### Available policy recipe

- every push: `tlaw check` (deterministic);
- law meaning changed (per `tlaw diff`): `tlaw audit` required before merge;
- scheduled: `tlaw check --search`, seed recorded, discoveries filed with
  their replay data.

All commands in that recipe exist. Whether a law change blocks a merge and
which audit must accompany it remain repository policy, not hidden tlaw state.

## Acceptance criteria

This design is an improvement only if all of the following hold:

1. a PR that narrows a literal domain produces a one-line "domain narrowed"
   entry in its own CI check;
2. a domain change that cannot be classified is reported as changed, never
   silently classified;
3. `tlaw diff` produces its report without importing either revision's code;
4. a commit touching laws and implementation together still presents the law
   changes as a distinct review section;
5. check output states the number of laws run and their declared scope, and
   claims nothing about code without laws;
6. a failure recorded on one machine replays to the same counterexample on
   another; and
7. a `--search` discovery replays from its recorded seed.

Criteria 1–4, 6, and 7 are covered by executable probes. Criterion 5 remains
partial: one selected law file reports its pytest law count and `--explain`
scope, while folder mode lists every discovered law file and verdict. tlaw
still does not enumerate project-wide code with no laws and therefore does not
yet emit an `unknown` map.

## Deliberately unresolved

- What "unknown" enumerates beyond the checked files — a boundary map of the
  project is a later derived view, and a wrong one would overclaim in the
  other direction.
- Rename detection — current `tlaw diff` reports a renamed law as removed plus
  added, which is honest but noisy.
- Whether a body change whose behavior is provably identical can ever be
  classified as such — likely not without execution, so likely never.
- Report signing or attestation for evidence that crosses trust domains.
