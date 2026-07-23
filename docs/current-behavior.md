# Current tlaw behavior

Status: implemented and verified on 2026-07-23. This document describes what
the current repository code does today. Design documents may describe future
directions; this file does not.

## The shortest usable path

```python
import tlaw

from implementation import reverse


@tlaw.law
def reversing_twice_returns_the_original(values: list[int]) -> bool:
    return reverse(reverse(values)) == values
```

```console
tlaw check laws.py --explain
```

The function name is the law description. tlaw generates values from the
parameter annotation, runs the law up to 100 times, and asks Hypothesis to
shrink a failure to a small input. Inferred laws are deterministic and do not
use Hypothesis's local example database. `--explain` prints the Hypothesis
version, visible input domains, example budget, and deterministic posture.

An inferred law may return:

- one Python `bool`;
- a `tlaw.Comparison` from `tlaw.same(...)`;
- a `tlaw.Refusal` from `tlaw.raises(...)`;
- a path `Equation` from `.same_as(...)`; or
- `None`, which is useful when the body raises its own assertion.

Any other return value, whether truthy or falsy, is rejected. This keeps
array-wide results such as NumPy's boolean arrays from being guessed as one
answer.

Before an inferred law runs, tlaw deep-copies every generated argument. A law
that passes after persistently changing one of those arguments is rejected. If
an argument cannot be copied or safely compared with its copy, tlaw errors
instead of claiming purity. This guard does not detect a mutation that is
reversed before return, external I/O, global state, or another hidden effect.

## Static law changes

`tlaw diff BASE..HEAD` reads the tracked Python source at two Git commits and
reports only changes to functions decorated with `tlaw.law`, with an aliased
`tlaw` or `tlaw.authoring` module, or with a `law` name imported directly from
either module:

```console
tlaw diff origin/main..HEAD
```

A law's identity is `module/path.py::function_name`. The report can contain
`added`, `removed`, `description changed`, `domain narrowed`, `domain
widened`, `domain changed`, `generation changed`, `decorators changed`, `body
changed`. More than one classification is shown when more than one part
changed.

Both revisions are read with `git cat-file` and parsed with Python's AST. They
are never checked out, imported, or executed. Formatting, comments, and a
function's leading docstring do not change its parsed body. Simple module-level
aliases used by annotations are expanded for comparison, including literal
`number`, `integer`, `one_of`, `list_of`, `list[...]`, and supported
`Annotated` shapes.

Narrowing or widening is reported only when the old and new literal domains
prove a subset relation. Dynamic bounds, dependent builders, mixed
narrowing/widening, or any unsupported expression become `domain changed`.
tlaw does not guess. Switching between inferred and explicit authoring is
`generation changed`; changing another decorator, including a skip or
Hypothesis settings decorator, is `decorators changed`. A rename is a removal
plus an addition. Helpers and module globals are deliberately not chased:
keep the contract's meaning in the decorated law body. Their changes remain
visible in the ordinary Git diff.

Exit code `0` means no law changed, `1` means the report contains reviewable
law changes, and `2` means the Git range or source could not be read.

## Randomized search and retained replay

Inferred `tlaw check` remains deterministic by default. Search is explicit:

```console
tlaw check laws.py --search --json failure.json
```

`--search` chooses a fresh non-negative seed, prints it, disables
derandomization for inferred laws, and applies the seed to every collected
tlaw law. `--seed NUMBER` repeats a recorded search sequence:

```console
tlaw check laws.py --search --seed 424242 --json repeated.json
```

Every check now builds a structured `tlaw.check/v1` report; `--json` retains
it. The report records:

- the law file and its SHA-256 digest;
- requested default or search mode, each failure's actual generation posture,
  the search seed, and Hypothesis version;
- each failing law's module-path/function identity and source line;
- the final shrunk generated inputs as typed JSON;
- the exception type and complete message; and
- for `tlaw.same`, both compared values, both tolerances, and the first
  difference.

Hypothesis reruns every minimal failing example once for final reporting; the
capture hook retains only those final reruns, not intermediate shrink
candidates. It freezes the input before calling the law, so even a failure
caused by the input-mutation guard replays from the value Hypothesis actually
generated. Distinct failure origins from one law remain distinct. Replay uses
those exact inputs rather than running another search:

```console
tlaw check --replay failure.json
```

Only failures named by the report are collected. Other tlaw laws and ordinary
pytest tests in the file do not run. Replay exits `0` when every retained input
produces the same exception type and message, `1` when an input passes or
fails differently, and `2` for malformed evidence, missing law identities,
timeouts, or other harness failures.

Typed input encoding supports `None`, booleans, integers (without JSON's
integer-size ambiguity), finite or non-finite floats, strings, bytes, lists,
tuples, dictionaries, sets, and frozensets, recursively. A counterexample
containing another type is still a real law failure, but it is not replayable
evidence: `check` exits `2`, names the unsupported input, and the CLI does not
write the requested JSON file.

Law paths are relative when the law lives below the directory where `check`
was launched. Run search and replay from the repository root so a report can
move with the repository to another machine.

## Visible input domains

Ordinary annotations such as `int`, `str`, and `list[int]` use Hypothesis's
type inference. Four concrete tlaw domain builders are also implemented:

```python
Reward = tlaw.number(0, 1)
Gpus = tlaw.integer(1, 8)
Rewards = tlaw.list_of(Reward, min_size=1, max_size=20)


@tlaw.law
def rewards_stay_in_range(rewards: Rewards) -> bool:
    return all(0 <= reward <= 1 for reward in rewards)
```

`number(low, high)` generates finite floats with both bounds included. It
rejects non-numeric, non-finite, or reversed bounds when the domain is defined.

`integer(low, high)` generates bounded integers, both bounds included, with
the same definition-time validation. It exists because the kvfit probe
showed real laws need bounded counts (GPUs, heads) and an unbounded `int`
annotation can hang code that scales with the generated value.

`one_of(values)` generates one of a finite collection given explicitly. It
refuses an empty collection at definition time, because a domain that can
generate nothing would make every law using it vacuous. Its description
lists the values, or summarises them with an honest count when there are
many: `one_of(0, 1, 2, 3, ... (20 values))`.

`list_of(element, min_size=0, max_size=None)` generates lists from either a
tlaw domain or a plain type. Its size bounds are checked when the domain is
defined. Domain descriptions appear in `--explain`, for example:

```text
rewards: list_of(number(0, 1), min_size=1, max_size=20)
```

Bare runtime domains used as annotations must be module-level names so
postponed string annotations can resolve them. They are values rather than
typing types, so strict static type checkers may mark `rewards: Rewards` as an
invalid type expression. The checker-clean spelling wraps the domain in
`Annotated`, where the base type is for the reader and the checker while
tlaw enforces the domain — including through PEP 695 `type` aliases and
inside `list[...]`:

```python
from typing import Annotated


type Reward = Annotated[float, tlaw.number(0, 1)]


@tlaw.law
def rewards_stay_in_range(rewards: list[Reward]) -> bool:
    return all(0 <= reward <= 1 for reward in rewards)
```

Exactly one domain per `Annotated` value is enforced. Any other `Annotated`
metadata, and any domain in a position tlaw cannot generate from yet (dict
values, tuples, custom classes), is rejected at decoration time rather than
silently discarded — the visible contract must never claim more than
generation delivers.

The `Annotated` base type is checked against the domain. When the base can
only be a type the domain never produces — `Annotated[str, tlaw.number(0, 1)]`,
which claims `str` while generating floats — it is refused at decoration
time, so the visible contract cannot lie. The check only refuses a mismatch
it can prove: a permissive base (`object`, `Any`, a union that includes the
generated type) is accepted, and a base tlaw cannot reduce to a concrete
type is left alone rather than guessed.

`--explain` expands an aliased domain to the domain it names, so
`rewards: list[Reward]` with `type Reward = Annotated[float, tlaw.number(0, 1)]`
is reported as `rewards: list[number(0, 1)]` — the generated domain is visible
in the explanation line, not hidden behind the alias.

### Dependent domains

When valid inputs relate two parameters, `tlaw.depends(builder)` derives one
domain from another's generated value. The builder's parameter names are the
dependencies, matched against the law's own parameters:

```python
Budget = tlaw.integer(1, 30)
Lengths = tlaw.depends(
    lambda budget: tlaw.list_of(tlaw.integer(1, budget), max_size=12)
)


@tlaw.law
def no_batch_exceeds_the_budget(budget: Budget, lengths: Lengths) -> bool:
    return all(sum(batch) <= budget for batch in pack(lengths, budget))
```

`one_of` composes with it when the valid inputs are a computed set rather
than a range:

```python
Gpus = tlaw.integer(1, 8)
TensorParallel = tlaw.depends(
    lambda gpus: tlaw.one_of(
        [size for size in range(1, gpus + 1) if gpus % size == 0]
    )
)
```

tlaw draws dependencies first and builds the dependent domain from their
values, so the relation holds **by construction**. Nothing is filtered,
therefore no example is discarded and no law can quietly become vacuous.
`--explain` reports `lengths: depends(budget)` — the declared contract is
the generated one.

Dependencies may chain and fan out: several parameters may share one
dependency, and a builder may name more than one. Unknown dependency names
and cycles are refused at decoration time. A builder that returns anything
other than a tlaw domain is refused when it runs.

The explicit `@tlaw.law("description", rewards=Rewards)` form also accepts
domains (or Hypothesis strategies) directly. It deliberately follows
ordinary Hypothesis settings — authors stack their own `@settings` on top,
and Hypothesis rejects a second application, so tlaw cannot force defaults
without breaking that — and it does not apply the inferred form's
input-mutation guard. That posture difference is stated per law in
`--explain`:

```text
divisors never exceed their number: gpus: integer(1, 8); explicit
strategies; Hypothesis settings decide examples and randomization; no
input-mutation guard
```

## One owned comparison

`tlaw.same(left, right, within=0.0)` compares values structurally and retains
both sides plus the first difference:

```python
@tlaw.law
def centered_rewards_sum_to_zero(rewards: Rewards) -> tlaw.Comparison:
    centered = center(rewards)
    return tlaw.same(sum(centered), 0.0, within=1e-12)
```

Two numeric tolerances are available, both finite, non-negative, and
defaulting to `0.0` (exact). They apply to numeric leaves only and combine
like `math.isclose` — a gap passes within *either*:

- `within` — an **absolute** tolerance, correct near zero (advantages,
  residuals, centered rewards), where relative tolerance is meaningless
  because the magnitude is zero; and
- `relative` — a fraction of the larger magnitude, correct for values whose
  scale is unknown or large, where a fixed absolute tolerance is either
  uselessly strict or falsely loose:
  `gap <= max(within, relative * max(abs(left), abs(right)))`.

Comparing against zero needs `within`: `relative` alone can never close a
gap to zero. A failure names whichever tolerances were set and the
allowance they produced at that magnitude. Two infinities that are not
exactly equal never pass a finite tolerance.

The [rescale example](../examples/rescale/README.md) is a numerical-stability
refactor whose correctness only holds to relative precision.

The current structural rules are:

- booleans compare only with booleans, so `True` does not equal `1`;
- integers and floats compare numerically with the declared tolerance;
- NaN never compares equal; equal infinities do;
- strings and bytes compare exactly and preserve their type distinction;
- lists and tuples compare item by item;
- dictionaries require the same keys and compare values recursively;
- objects exposing `shape`, `dtype`, and `tolist()` have their explicit shapes
  compared first, then are converted to nested Python values. Checking shape
  separately distinguishes empty arrays such as `(0, 2)` and `(0, 3)`, which
  both become `[]`, without making NumPy a dependency; and
- other objects fall back to `==`, but only one Python boolean is accepted.

Return the `Comparison` directly from a law to keep its explanation. Combining
it with `and` or `or` first converts it to a boolean and loses the retained
first-difference report.

## One exception contract

`tlaw.raises(Expected, function, *arguments)` states that the call must
raise `Expected` (a subclass counts, matching ordinary `except`):

```python
@tlaw.law
def an_oversized_sequence_is_refused(raw: RawLengths, budget: Budget):
    lengths = fitted(raw, budget) + [budget + 1]
    return tlaw.raises(ValueError, pack, lengths, budget)
```

tlaw performs the call and retains the outcome, so a failing law names
what actually happened — "the call returned (...) instead of raising" or
"the call raised TypeError: ..." — rather than reporting `False`. Any
other `Exception` becomes that evidence; `KeyboardInterrupt` and other
non-`Exception` exits propagate. The expected type must be an `Exception`
subclass, checked at the call site.

## Stateful paths

For mutable behavior, `tlaw.system(...)` defines how to construct a fresh
value and how to observe it. Calling the system creates an immutable path.
`.call(...)`, `.do(...)`, and `.then(...)` append steps; `.same_as(...)`
creates an equation between two paths. Each side builds a fresh start before it
runs, so one path cannot mutate the other's starting object.

This path vocabulary is also the implemented categorical core: an internal
arrow is an immutable tuple of steps, composition concatenates tuples, and the
identity is the empty tuple. Associativity and left/right identity therefore
hold structurally.

## Probing another project

Laws may target any installed codebase before it adopts tlaw. From a
scratch directory containing only a laws file that imports the target's
real functions:

```console
uv run --project path/to/project --with path/to/tlaw tlaw check laws.py
```

`--project` supplies the target's environment and `--with` injects tlaw for
that run only, so the target repository is read, not modified. This is the
protocol recorded in `docs/kvfit-probe.md`. `tlaw mutate` uses the same
external laws-file pattern for package targets: it copies the target package
into an isolated run and places that copy before the installed package. Its
mandatory canary refuses to score the run if the laws did not actually
observe that copy.

## Commands and exit behavior

| Command | Implemented behavior | Exit behavior |
| --- | --- | --- |
| `tlaw check laws.py` | Runs one Python law file with third-party pytest plugin autoload disabled and constructs replay evidence for a failure. | `0` for pass, `1` for a retained law failure, `2` when the failure or harness cannot produce valid evidence. |
| `tlaw check feature/` | Discovers every `laws.py` and `*_laws.py` under a folder and checks each. Per-file evidence flags (`--search`, `--json`, `--seed`, `--replay`) are refused in folder mode. | Worst per-file code: `0` only when every file passes, else `1` or `2`. |
| `tlaw check laws.py --explain` | Adds the inferred-generation explanation before the pytest result. Aliased domains are expanded. | Same as `check`. |
| `tlaw check laws.py --search [--seed N]` | Uses a fresh visible seed, or reuses `N`, instead of deterministic inferred generation. | Same as `check`. |
| `tlaw check laws.py --json report.json` | Writes the stable `tlaw.check/v1` report; it composes with `--search`. | Same as `check`. |
| `tlaw check --replay report.json` | Runs exactly the retained law inputs and no neighboring tests. | `0` when every failure is reproduced exactly, `1` when behavior differs, `2` for invalid evidence. |
| `tlaw diff BASE..HEAD` | Parses tlaw laws from two Git revisions without executing them and reports the law-space difference. | `0` for no law changes, `1` when law changes are present, `2` for an invalid range or unreadable source. |
| `tlaw audit audit.py` | Runs one declared baseline and declared known-wrong implementations from a Python `Lawbook`. Legacy TOML manifests also work. | `0` when all declared wrong implementations are rejected, `1` when one survives, `2` for invalid evidence. |
| `tlaw audit audit.py --json report.json` | Also writes the stable `tlaw.audit/v1` JSON report. | Same as `audit`. |
| `tlaw mutate laws.py implementation.py` | Generates deterministic, single-site mutations of one implementation module and runs the unchanged laws in isolated copies of the selected directory or package boundary. | `0` when no unexplained survivor or harness error remains, `1` when a survivor remains, `2` for invalid baseline or harness errors. |
| `tlaw mutate ... --json report.json` | Also writes the stable `tlaw.mutation/v1` JSON report. | Same as `mutate`. |

`mutate` supports two boundary shapes, chosen automatically:

- **directory** — laws and implementation in one flat folder, which is
  copied whole; the laws import the module as a sibling
  (`from implementation import ...`).
- **package** — the implementation is inside a package, such as
  `src/kvfit/planner.py`. The whole package is copied and placed ahead of
  the installed one on the import path, so intra-package imports resolve
  inside the mutated copy. The laws may live anywhere and import the package
  normally (`from kvfit.planner import ...`). A regular package is found by
  its `__init__.py` chain; a namespace package (PEP 420, no `__init__.py`)
  is found by reading the dotted name from the laws' own import statement,
  matched against the module's path.

The report names the shape and the dotted module, for example
`Boundary: package (kvfit.planner)`, and mutation IDs hash that dotted name
so identifiers stay unique across packages. In either shape the canary runs
first: if the copy fails to shadow the installed code — an editable install
using an import hook rather than a path entry, for instance — the run
reports invalid evidence rather than a page of false survivors.

For `mutate`, the law file and implementation file must be different `.py`
files, and for the flat shape they must share one directory. The
unmodified baseline must pass first. Its measured duration determines each
mutant's timeout budget. A law failure, implementation exception, or calibrated
timeout kills a mutant; every kill is replayed once. Collection failure under
a mutant is invalid rather than a kill, and a non-reproducing kill is an error.
The rejecting law is reported as a portable `laws.py::function_name` identity,
not as a path through tlaw's temporary mutation workspace.

Before any mutant is scored, `mutate` runs a canary by default, with no
opt-out: a poisoned copy of the implementation in which every function
raises. If the laws still pass, they never observed the boundary module —
usually because they import an installed package rather than the sibling
file — and the run reports `INVALID EVIDENCE` with exit code `2` instead of
listing survivors that would mean nothing. The canary verdict appears in
both the text report and the JSON `canary` field.

The law-first feature workflow built on these guarantees — laws before
implementation, in a flat mutate-ready folder — is demonstrated end to end
in [examples/score_weights](../examples/score_weights/README.md), including
a real surviving mutant explained in a committed equivalence ledger.

The current mutation operators work only inside function and method bodies:

- swap `+`/`-` or `*`/`/`;
- change `<`/`<=` or `>`/`>=`;
- flip a returned boolean or add one to a returned numeric constant;
- replace a standalone function or method call with `pass`; and
- negate an `if` or `while` condition.

Every survivor remains visible. A committed TOML equivalence ledger may attach
a reason to a survivor with `--equivalents`; it reclassifies that survivor as
an unresolved equivalent candidate rather than removing it. Missing or
contradicted entries are printed as stale.

## Python entry points

The top-level package exports `law`, `system`, `same`, `raises`, `number`,
`integer`, `one_of`, `list_of`, `depends`, `check`, `check_folder`,
`discover_law_files`, `replay`, `diff`, `audit`, and `mutate`, plus their result
and error types, including `FolderCheckResult`. Runner functions return
structured objects with text and JSON renderers where applicable.
`AuditResult.enforce()`
raises `tlaw.EnforcementError` unless the declared audit is sufficient.
Mutation results deliberately have no broad "correct" or "covered" verdict;
they report the generated challenges that were killed, survived, or could not
produce evidence.

## Not implemented

- arbitrary or composable domain predicates beyond the implemented numeric,
  finite-choice, list, and dependent domains;
- domains inside dictionaries, tuples, custom classes, or other unsupported
  nested positions;
- configurable NaN equality;
- opt-in loading of third-party pytest plugins;
- automatic pipeline tracing, dependency maps, contract-conflict planning, or
  project-wide graphs;
- a map of code with no laws, so `check` cannot report project-wide unknown
  behavior; and
- mutating two packages at once (one module at a time is supported, across
  flat, regular-package, and namespace-package boundaries; compiled modules
  are refused because a source-level tool cannot mutate them).

Neither writing nor enforcing a law requires an LLM or network access. Python
law files, Python audit files, and the application code they import are
executable code and should only be run when trusted.
