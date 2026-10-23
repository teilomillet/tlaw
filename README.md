# tlaw

tlaw lets ordinary Python code state behavior that an implementation must
continue to satisfy. The law file is executable and is the source of truth;
Hypothesis generates and shrinks inputs underneath it, or an explicit exhaustive
check enumerates a small finite domain.

For the exact implemented surface and its limits, see
[current tlaw behavior](https://github.com/teilomillet/tlaw/blob/main/docs/current-behavior.md). Design documents are kept
separate because they may describe syntax that does not exist yet.

## Install

```console
uv add --dev tlaw        # inside a project (recommended)
uv tool install tlaw     # or: the CLI on its own
```

The project install is recommended because laws are ordinary Python: when a
law imports your project's own dependencies, tlaw must run in the same
environment (`uv run tlaw check laws.py`). The standalone tool works for
self-contained law folders whose implementation needs only the standard
library.

For a short complete workflow, follow the [check, retain, replay, and mutation
example](examples/workflow/README.md). It includes a deliberate bug and explains
the exit codes when replay reproduces a failure and when the code is repaired.

## What works today

tlaw currently has a small beginner interface plus explicit tools for narrower
domains, structural comparison, stateful paths, and mutation evidence:

- `@tlaw.law` turns an ordinary annotated Python function into an executable
  law;
- simple annotations such as `str`, `int`, and `list[int]` supply generated
  inputs;
- `number(...)`, `integer(...)`, `one_of(...)`, `list_of(...)`, and
  `recursive(...)` define visible finite-float, bounded-integer,
  finite-choice, list, and finite recursive domains without Hypothesis syntax;
- `record(...)` composes named fields into a dictionary or a constructed
  value, and nests, so a whole structured request is one visible domain;
- `depends(...)` derives one parameter's domain from another's generated
  value, so relational inputs are generated correctly by construction;
- `same(..., within=..., relative=...)` compares nested values and array-like
  values with explicit absolute and relative tolerances and reports the first
  difference, and `.and_then(...)` adds another claim without losing that
  report;
- `raises(Expected, function, ...)` states an exception contract whose
  failure names what actually happened instead of returning False, and awaits
  an `async` target;
- `returns(Branch, function, ...)` states the same kind of contract for code
  that returns its failure instead of raising it, and `.and_then(...)` adds a
  second claim without losing the first one's explanation;
- `excludes(value, *fragments)` states that no internal detail leaks,
  searching nested structures and reporting where a fragment was found;
- `tlaw check laws.py --explain` runs the laws and reports what it generated;
- `tlaw check laws.py --exhaustive` checks every combination of supported finite
  inputs, with separate counts and completion evidence;
- `tlaw check feature/` discovers and checks every law file under a folder;
- `tlaw check laws.py --search --json failure.json` uses a fresh visible seed
  to look for new counterexamples and retains exact replay evidence;
- `tlaw check --replay failure.json` reruns only those retained failures;
- `tlaw diff origin/main..HEAD` statically separates law changes from the
  surrounding implementation diff without executing either revision;
- inferred laws use deterministic runs and show Hypothesis's minimized failing
  input;
- checks account for every selected law case, so skipped, expected-failure,
  and unrun laws cannot silently pass, and replay requires actual execution;
- a passing inferred law is rejected when tlaw detects that it mutated its
  generated input;
- `system`, `call`, `then`, and `same_as` cover explicit stateful paths; and
- `tlaw mutate laws.py implementation.py` challenges the laws with generated
  mutations of one module and shows every survivor, for a flat feature folder
  or a module inside an installed package; add `--exhaustive --max-cases 1000`
  to enumerate declared finite inputs and report each law's bounds and case
  counts while preserving exact replay and calibrated timeout confirmation.

Use `tlaw mutate laws.py implementation.py --plan` to execute one isolated
baseline and inspect the campaign's planned work. Add `--max-runs 20` to cap all
isolated pytest invocations, including the canary and confirmations; reports
explicitly identify budget stops, unconfirmed candidates and mutations left unrun.

Important pieces are still intentionally absent: domain predicates and nested
domain positions beyond the implemented `number`, `integer`, `list_of`,
`one_of`, `recursive`, `record`, `depends`, and supported `Annotated` forms;
relations across a record's field scope and a law's parameter scope; observing
that a collaborator was *not* called, which is a decided non-goal — tlaw sequences
the calls a law makes and does not spy; opt-in third-party pytest plugins;
and impact graphs.

## Write a first law

Given an ordinary function:

```python
# implementation.py
def reverse(values: list[int]) -> list[int]:
    return list(reversed(values))
```

Its law can be one function:

```python
# laws.py
import tlaw

from implementation import reverse


@tlaw.law
def reversing_twice_returns_the_original(values: list[int]) -> bool:
    return reverse(reverse(values)) == values
```

Run it:

```console
tlaw check laws.py
```

To see what tlaw inferred rather than taking generation on trust:

```console
tlaw check laws.py --explain
```

The function name explains the behavior, its annotation supplies generated
inputs, and the returned boolean is what must always hold. Writing this first
law requires no Hypothesis syntax.

Bare laws run up to 100 deterministic examples without a local example
database. tlaw includes the minimized failing input in its output. It also
copies generated inputs and rejects a passing bare law when it detects that the
law mutated one; stateful behavior should use the explicit path interface
below.

## The vocabulary is a ladder

You now know the whole first rung: `@tlaw.law` and a returned `==`. Everything
else is an escalation you reach for **only when the rung below cannot say what
you mean** — most laws never leave the first rung. The same module docstrings
(`import tlaw; help(tlaw)`) carry this map.

What the law returns:

| Reach for | When the claim is |
| --- | --- |
| `return a == b` | exact equality of ordinary values — start here |
| `same(a, b, within=, relative=)` | equal *within tolerance*, or nested/array/float values |
| `raises(Exc, f, ...)` | a call must **raise** |
| `returns(Type, f, ...)` | a call must **return** an error-value branch (`Err`), not raise |
| `excludes(text, *forbidden)` | something must **not** appear (a leaked secret, hostname, traceback) |
| `raises(...).and_then(observe)` | one call carries **two** claims (it refused, *and* its message is redacted) |
| `same(...).and_then(next_claim)` | equivalence is only the first claim (the results agree, *and* both runs did the expected work) |

What the annotation generates:

| Reach for | When |
| --- | --- |
| `values: list[int]` | a plain type covers the real inputs — start here |
| `number(lo, hi)` · `integer(lo, hi)` · `one_of(...)` | the plain type is broader than the real domain |
| `list_of(...)` | a list of a domain, with visible size bounds |
| `recursive(base, extend, ...)` | values recursively contain the same visible domain, within a finite leaf budget |
| `depends(...)` · `record(...)` | inputs are **related**: one bounds another, or a record's fields must agree |

When behavior mutates state, describe [two paths from fresh starts](#describe-stateful-paths)
instead of a returned value.

Getting evidence is a separate axis, ordered by how hard the laws are tried:
`tlaw check` (do they hold?) → `tlaw mutate` (do they *constrain*?) →
`tlaw diff` (did a commit change a law's *meaning*?) → `tlaw audit` (do they
reject declared-wrong implementations?).

## Make law changes loud

Git remains the acceptance ledger: a committed law is accepted behavior.
`tlaw diff` makes changes in that behavior visible on their own:

```console
tlaw diff origin/main..HEAD
```

```text
tlaw law-space diff 62ea585dd296..a482e8d8c430
Law changes:
- feature/laws.py::result_stays_bounded: domain narrowed
- feature/laws.py::packing_is_stable: body changed
Changed laws: 2
```

The command reads source blobs directly from Git and parses them; it never
checks out, imports, or executes historical code. A law is identified by its
module path and function name. Added and removed laws, descriptions, domains,
generation posture, other decorators, and function bodies are compared as
parsed Python, so formatting alone is ignored. Adding `@pytest.mark.skip` or
moving from inferred to explicit generation is therefore visible.

Literal `number`, `integer`, `one_of`, and `list_of` changes are called
`domain narrowed` or `domain widened` only when subset/superset is provable.
A dynamic or otherwise incomparable edit is simply `domain changed`. Exit
code `0` means no law change, `1` means law changes need review, and `2` means
the Git range or source could not be read honestly. A rename is intentionally
reported as one removal and one addition.

## Check every input in a small domain

Declare a bounded domain and opt into enumeration:

```python
import tlaw

Count = tlaw.integer(0, 3)


@tlaw.law
def boolean_addition_stays_bounded(count: Count, enabled: bool) -> bool:
    return 0 <= count + enabled <= 4
```

```console
tlaw check laws.py --exhaustive --max-cases 1000 --json report.json
```

This checks all eight combinations. The default limit is 1,000 combinations per
law case. Supported inputs are `bool`, `None`, `Literal`, `integer`, `one_of`,
bounded `list_of` over finite elements, and dictionary-form `record` over finite
independent fields, including aliases and supported `Annotated` forms. Larger
or unsupported domains are refused before the laws run; tlaw never silently
switches to sampling.

For example, `tlaw.list_of(tlaw.one_of(["left", "right"]), max_size=4)`
enumerates all 31 histories of lengths `0..4`, including the empty list. Nested
finite lists work too. Normal output reports the length bounds beside the
checked/total case counts; JSON records them in each obligation's `list_bounds`.
The case cap is checked before lists are generated.

Records compose with lists: a command with two kinds and three amounts has six
values; histories of those commands with `max_size=2` have 43 cases. Nested
records retain field names in list-bound paths such as `request.options.flags`
and `history[].options.flags`. Constructor-based records and dependent fields
remain sampled-only.

Output and JSON distinguish `exhaustive`, `sampled`, and `replay` evidence. Only
an exhaustive check that passes every declared combination reports `exhausted`.
A failure stops that law at its first counterexample, which can be retained and
replayed with the same commands below. Ordinary checks continue to use Hypothesis;
mutation remains a separate challenge to the laws. See
[finite checking](docs/current-behavior.md#exhaustive-finite-checking) for limits.

## Search and replay a counterexample

Normal checks are deterministic. Randomized discovery is an explicit command:

```console
tlaw check laws.py --search --json failure.json
```

tlaw prints the chosen seed and stores a `tlaw.check/v1` report containing the
law identity, Hypothesis version, shrunk generated inputs, exception
fingerprint, and both values when the law returned `tlaw.same(...)`.

Reproduce only those retained failures with:

```console
tlaw check --replay failure.json
```

Replay exits `0` only when every recorded input still produces the same
exception type and message. It exits `1` when the counterexample no longer
fails in the recorded way, and `2` when the report or its law identity is
invalid. An unrelated pytest test in the law file is not run during replay.

To rerun the whole randomized search rather than only its shrunk result, reuse
the displayed seed:

```console
tlaw check laws.py --search --seed 424242
```

Typed replay encoding currently supports `None`, booleans, integers, floats,
strings, bytes, lists, tuples, dictionaries, sets, and frozensets, recursively.
If a failing input cannot be encoded without guessing, tlaw reports invalid
evidence and does not write the requested report. Inputs are frozen before
the law call, so a mutation-guard failure replays from the value that was
actually generated, not the value after the law changed it.

## State a narrower domain and comparison

When a plain type is broader than the application's real inputs, name the
accepted values beside the laws:

```python
import tlaw

from rewards import center


Reward = tlaw.number(0, 1)
Rewards = tlaw.list_of(Reward, min_size=1, max_size=20)


@tlaw.law
def centered_rewards_sum_to_zero(rewards: Rewards) -> tlaw.Comparison:
    return tlaw.same(sum(center(rewards)), 0.0, within=1e-12)
```

`number(0, 1)` means finite floats from `0` through `1`, including both
bounds. `list_of` makes the element and size domain visible in `--explain`.
`within=1e-12` means absolute tolerance at every numeric leaf; omitting it
means exact comparison. For values whose magnitude is large or unknown, add
`relative=1e-9` — the two combine like `math.isclose`, and the
[rescale example](https://github.com/teilomillet/tlaw/blob/main/examples/rescale/README.md)
shows why a numerical refactor needs it. `same` also compares nested lists,
tuples, dictionaries, and array-like objects exposing `shape`, `dtype`, and
`tolist()`. Recursive list and dictionary graphs terminate: revisiting the
same left/right container pair means that relation is already established,
while a difference on any finite path is still reported.

A recursive input domain uses the same visible vocabulary:

```python
Tree = tlaw.recursive(
    tlaw.integer(0, 9),
    lambda child: tlaw.list_of(child, max_size=3),
    max_leaves=20,
)
```

The builder must accept exactly one child domain, compose it through a tlaw
domain builder, expose that child in the description, and keep the same visible
shape on every call. `min_leaves` and `max_leaves` count base values, not depth;
invalid, description-only, or vacuous declarations are rejected when the
domain is defined.

`same` states only a relationship. Two implementations that both return an
empty result satisfy an equivalence law even when the domain says each should
produce one item per input. Keep that second claim explanatory with
`Comparison.and_then(...)`:

```python
refactored = normalize(values)
baseline = normalize_reference(values)

return tlaw.same(refactored, baseline).and_then(
    lambda: tlaw.same(
        (len(refactored), len(baseline)),
        (len(values), len(values)),
    )
)
```

If equivalence fails, the report retains that comparison. Only after it
matches does tlaw evaluate and return the length comparison. A plain `and`
would convert the first failure to `False` and discard its left, right, and
first difference.

Domain aliases are runtime values, so strict type checkers may flag
`rewards: Rewards`. The checker-clean spelling wraps the domain in
`Annotated` — `type Reward = Annotated[float, tlaw.number(0, 1)]` used as
`rewards: list[Reward]` — where the base type is for the checker and tlaw
enforces the domain; see
[current tlaw behavior](https://github.com/teilomillet/tlaw/blob/main/docs/current-behavior.md#visible-input-domains).
`Annotated` metadata other than exactly one tlaw domain, and domains in
positions tlaw cannot generate from yet, are rejected rather than silently
ignored. A base type that contradicts its domain — `Annotated[str,
tlaw.number(0, 1)]` — is refused too, so the visible contract cannot claim a
type the domain never produces.

The complete runnable version is in the
[rewards example](https://github.com/teilomillet/tlaw/blob/main/examples/rewards/README.md).

For a larger first exercise, open the self-contained
[name cleaner tutorial](https://github.com/teilomillet/tlaw/blob/main/examples/name_cleaner/README.md). It explains every
definition, includes implementation and laws together, and gives a deliberate
break-and-fix exercise. The tiny [list example](https://github.com/teilomillet/tlaw/blob/main/examples/list_tools/laws.py)
and the current [interface direction](https://github.com/teilomillet/tlaw/blob/main/docs/interface-direction.md) remain
available as references.

Keep laws beside the code whose behavior they define. A feature folder should
remain understandable on its own; tlaw does not require a distant contract
registry or hidden configuration for a local law.

## Git and CI are the acceptance mechanism

A law committed to the repository is accepted behavior. tlaw does not maintain
a second approval database. Law changes are ordinary reviewable code changes,
and the repository's CI should enforce the resulting source of truth:

```console
tlaw check path/to/feature/laws.py --explain
```

This repository enforces itself with one visible gate, `scripts/gate`: the
committed dependency lock, ruff, the full property suite, independently
confirmed CrossHair contracts, tlaw's self-laws, `tlaw check` on the whole
`examples/` folder, and `tlaw mutate` on the flat examples. A separate
scheduled and path-triggered workflow runs the slower full mutation
grade for the core value boundaries. The exact claim and its limits are in
[core evidence](https://github.com/teilomillet/tlaw/blob/main/docs/core-evidence.md).
CI pins uv and runs the gate on every push and pull request
(`.github/workflows/ci.yml`), and the same file runs locally before each push
once the hook is installed:

```console
git config core.hooksPath .githooks
```

Local and remote checks share one script instead of maintaining two command
lists. CI also pins uv, and the gate refuses a stale dependency lock.

The gate also builds the source distribution and wheel, installs the wheel with
locked runtime dependencies in a fresh environment, and runs the worked example
outside the checkout. It verifies sampled and exhaustive checks, retained
counterexample JSON, replay before and after a repair, and sampled and exhaustive
mutation kills confirmed by exact replay. Run just this check with
`uv run python scripts/check_installed.py`. The release workflow uses the same
check on the exact wheel it uploads.

Tlaw also checks itself through the [self-law suite](selflaws/README.md).
Four law files exercise replay encoding, comparison, domains, and paths against
the real core and ten declared known-wrong implementations. Independent pytest
checks require the intended rejections, detect an always-accepting law checker,
and exercise exact counterexample replay. The gate runs this evidence alongside
the existing mutation challenges; it is bounded test evidence, not formal proof.
`git push --no-verify` skips the local run in an emergency; CI remains the
enforcement of record. Publishing stays separate: the release workflow is
gatekept by a version change in `pyproject.toml`.

If a commit intentionally changes behavior, its executable law and
implementation may change together. The law diff states what changed; CI
checks that the implementation now obeys it on every push and pull request.

## Probe a codebase without modifying it

Laws can be written against any installed project before that project
adopts tlaw. Put a laws file in a scratch directory, import the project's
real functions, and run tlaw inside the project's own environment:

```console
uv run --project path/to/project --with tlaw tlaw check laws.py
```

`--project` supplies the target's dependencies; `--with` injects tlaw for
the run only. Nothing in the target repository changes — no pyproject edit,
no committed files — which makes this the cheapest honest way to answer
"would laws hold on this code?" before adopting anything. The target's own
pytest `addopts` are deliberately not applied, so a project configured for a
plugin tlaw does not autoload still runs. The documented
[kvfit probe](https://github.com/teilomillet/tlaw/blob/main/docs/kvfit-probe.md)
and [anef probe](https://github.com/teilomillet/tlaw/blob/main/probes/anef/README.md)
ran this way and paid for features with their findings.

## Release to PyPI

Changing `[project].version` in `pyproject.toml` on `main` is the release
signal. The workflow compares that value with the version before the push. An
unchanged version never publishes; a changed version must pass the lockfile,
compiled-changelog, lint, test, build, and distribution checks before it
reaches PyPI.

During development, add one small file under `changes/` for each user-visible
change instead of editing `CHANGELOG.md`. The complete rules are in
[CONTRIBUTING.md](CONTRIBUTING.md). At release, use uv to keep the version and
lockfile together, then compile and review those fragments:

```console
uv version 0.0.3
uv run towncrier build --yes --version 0.0.3
git diff -- CHANGELOG.md changes pyproject.toml uv.lock
git add pyproject.toml uv.lock CHANGELOG.md changes/
git commit -m "chore(release): publish tlaw 0.0.3"
git push origin main
```

The upload uses PyPI trusted publishing, not a long-lived API token. Its
one-time configuration must name GitHub owner `teilomillet`, repository
`tlaw`, workflow `release.yml`, and environment `pypi`. The workflow is kept
in [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Describe stateful paths

When behavior mutates an object, a law can instead compare two paths from fresh
starts:

```python
from hypothesis import strategies as st

import tlaw
from wallet import Wallet


money = st.integers(min_value=0)
wallet = tlaw.system(
    Wallet,
    observe=lambda value: value.balance,
)


@tlaw.law(
    "deposit then withdraw changes nothing",
    balance=money,
    amount=money,
)
def deposit_round_trip(balance, amount):
    start = wallet(balance)
    return (
        start
        .call("deposit", amount)
        .call("withdraw", amount)
        .same_as(start)
    )
```

Run it in the same way:

```console
tlaw check laws.py
```

The stateful path interface adds four ideas:

- `system(...)` defines how to construct fresh values and what to observe;
- `@law(...)` supplies ordinary Hypothesis strategies;
- `.call(...)` or `.then(...)` extends a path; and
- `.same_as(...)` says two paths must agree.

Law functions do not need pytest's `test_` prefix. They are still executable
Hypothesis tests and can also be run directly with pytest.

See the complete [mutable wallet laws](https://github.com/teilomillet/tlaw/blob/main/examples/wallet/laws.py) and the smaller
[pure UTF-8 round-trip law](https://github.com/teilomillet/tlaw/blob/main/examples/codec/laws.py).

## Why paths

tlaw models each program as an immutable sequence of steps. Composition is
sequence concatenation, and the identity is the empty sequence. This gives the
categorical rules—associativity and left/right identity—structurally, before
application-specific testing begins.

A domain law is then an equation between two executable paths:

```text
                 deposit(amount)     withdraw(amount)
wallet(balance) ──────────────────────────────────────► result
       │
       └──────────────── do nothing ──────────────────► expected

                         result == expected
```

For mutable application code, each path constructs its own fresh objects. For
pure code, `.then(function)` uses the function's return value as the next value.

## Check versus audit

Human-authored laws and audit evidence are deliberately separate:

```console
tlaw check laws.py
tlaw audit audit.py
```

- `check` runs the laws against the current implementation.
- `audit` runs those same laws against a passing baseline and declared
  known-wrong implementations.

The wallet's [audit declaration](https://github.com/teilomillet/tlaw/blob/main/examples/wallet/audit.py) is separate from its
[laws](https://github.com/teilomillet/tlaw/blob/main/examples/wallet/laws.py), so someone writing a law does not need to think
about mutation testing or audit plumbing.

Run the intentionally weak audit:

```console
uv run tlaw audit examples/wallet/tlaw-weak.toml
```

It excludes the zero-transfer law and exposes the gap:

```text
Strength: 3/4 (75.0%)
Verdict: INSUFFICIENT
```

Run the complete declared audit:

```console
uv run tlaw audit examples/wallet/audit.py
```

```text
Strength: 4/4 (100.0%)
Verdict: SUFFICIENT FOR DECLARED KNOWN-WRONG SET
```

That verdict is deliberately narrow. It does not prove that the wallet is
universally correct. It establishes that the baseline passes and every
declared known-wrong implementation is rejected by a law.

## Challenge laws with generated mutations

The audit's known-wrong set is chosen by its author. `tlaw mutate` generates
challenges nobody selected — small single-site changes to one implementation
module — and runs the unchanged laws against every one:

```console
tlaw mutate laws.py implementation.py
```

```text
Canary: KILLED (laws observe the mutated module)
Mutations:
- 3e67f984ab5f SURVIVED double_all: replace value + value with value - value
- f3bb33e45d2b KILLED sign: replace value < 0 with value <= 0 (law: laws.py::sign_matches_a_reference)
Killed 3 of 4 runnable generated mutations.
Survivors: 1
```

The rules are recorded in [generated mutation evidence](https://github.com/teilomillet/tlaw/blob/main/docs/mutation-evidence.md)
and encoded in `tlaw/mutation.py`: the baseline must pass with complete law
obligations first. Ordinary mutant runs must execute the same law cases, and
kills must reproduce retained typed inputs and exception fingerprints against
the same mutant in a fresh copy. Calibrated timeout kills must time out again
after a fresh passing baseline recalibrates the budget. Missing or incomplete
evidence is an error; load failures are invalid; every survivor stays visible.

The JSON report retains law obligations, counterexamples, and replay evidence.
`replay_method` distinguishes `exact` input replay from `timeout` confirmation,
with the confirmation process's exit code, timeout flag, and duration recorded.

The canary runs by default before anything is scored: a poisoned copy of the
implementation, in which every function raises, must be rejected by the laws.
If it survives, the laws never observed the boundary module — typically
because they import an installed package instead of the sibling file — and
the report says INVALID EVIDENCE instead of listing meaningless survivors.
There is no opt-out; the canary protects exactly the user who has never
heard of it.

A survivor a human has investigated can be recorded in a committed TOML
ledger and passed with `--equivalents equivalents.toml`; entries contradicted
by evidence are reported as stale instead of being silently honored. Mutation
IDs are content-based, so they survive edits elsewhere in the module.

## Start a new feature law-first

For new code, the recommended shape makes mutation evidence free from day
one:

```text
feature/
├── implementation.py
└── laws.py            # imports: from implementation import ...
```

Write the laws first and watch them fail; implement until `tlaw check`
passes; then run `tlaw mutate` to measure whether the laws actually
constrain the implementation. Because the folder is flat and the laws
import the module by name, the mutation boundary is wired by construction —
the canary confirms it on every run.

Two worked examples record real runs end to end:
[examples/score_weights](https://github.com/teilomillet/tlaw/blob/main/examples/score_weights/README.md)
includes a genuine surviving mutant explained in a committed equivalence
ledger, and
[examples/batch_packer](https://github.com/teilomillet/tlaw/blob/main/examples/batch_packer/README.md)
is a full law-first development session — seven laws red before any
implementation existed, then 13 of 13 mutants killed — with its findings in
[the law-first experiment record](https://github.com/teilomillet/tlaw/blob/main/docs/law-first-experiment.md).

## Use it from Python

```python
import tlaw

check = tlaw.check("laws.py")
if not check.passed:
    print(check.output)

report = tlaw.audit("audit.py")
print(report)
report.enforce()

mutations = tlaw.mutate("laws.py", "implementation.py")
print(mutations)
```

`report.enforce()` raises `tlaw.EnforcementError` when evidence is invalid or
insufficient. Its `error.result` retains the structured audit evidence.

Existing TOML audit manifests remain supported. Python law and audit files are
executable code, so run only files you trust.

## Develop tlaw

tlaw is itself developed property-first:

```console
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
./scripts/crosshair
uv run tlaw check self_laws/
./scripts/core-evidence
```

The precise implemented surface is recorded in
[current tlaw behavior](https://github.com/teilomillet/tlaw/blob/main/docs/current-behavior.md), and the independent challenge
rules are recorded in
[generated mutation evidence](https://github.com/teilomillet/tlaw/blob/main/docs/mutation-evidence.md).
The repository-specific composition of symbolic, property, and mutation
evidence is recorded in
[core evidence](https://github.com/teilomillet/tlaw/blob/main/docs/core-evidence.md).
