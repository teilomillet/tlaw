# Current tlaw behavior

Status: implemented and verified on 2026-10-23. This document describes what
the current repository code does today. Design documents may describe future
directions; this file does not.

Maintenance: this file is written by hand and has no generator, so nothing
updates it automatically when behavior changes. Update it in the same commit
as the behavior change, and treat the following executable checks as the
primary pin for its claims: `tests/test_public_api.py` (exported names),
`tests/test_cli.py` (CLI commands and exit codes), `tests/test_exhaustive*.py`
and `tests/test_recursive_exhaustive.py` (finite checking),
`tests/test_obligations.py` (obligation accounting),
`tests/test_mutation_engine.py` and `tests/test_mutation_properties.py`
(mutation evidence), and `./scripts/gate` as the composition. A claim that
none of these exercises is prose only: verify it against the code before
repeating it.

This document is also the single canonical list of what is *not* implemented
(see [Not implemented](#not-implemented)); other pages link here instead of
restating it.

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
- a `tlaw.Comparison` from `tlaw.same(...)`, optionally chained through
  `.and_then(...)`;
- a `tlaw.Refusal` from `tlaw.raises(...)`, optionally chained through
  `.and_then(...)`;
- a `tlaw.Outcome` from `tlaw.returns(...)`, optionally chained through
  `.and_then(...)`;
- a `tlaw.Exclusion` from `tlaw.excludes(...)`, optionally chained through
  `.and_then(...)`;
- a path `Equation` from `.same_as(...)`; or
- `None`, which is useful when the body raises its own assertion.

Any other return value, whether truthy or falsy, is rejected. This keeps
array-wide results such as NumPy's boolean arrays from being guessed as one
answer.

Before an inferred law runs, tlaw deep-copies every generated argument. A law
that passes after persistently changing one of those arguments is rejected. If
an argument cannot be copied or safely compared with its copy, the run is
invalid evidence (exit `2`) instead of a law refutation; a genuine refutation
in the same file still decides the result. Known limitation: an unchanged value
whose deep copy nevertheless compares unequal (for example some
identity-equality objects) can still be reported as a refutation, because the
guard cannot distinguish that case from a real mutation. This guard does not
detect a mutation that is reversed before return, external I/O, global state, or
another hidden effect.

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

## Exhaustive finite checking

Ordinary checks use Hypothesis and report `sampled` evidence. Finite declarations
can instead be enumerated explicitly:

```console
tlaw check laws.py --exhaustive --max-cases 1000 --json report.json
tlaw check feature/ --exhaustive
```

The Python equivalents are `tlaw.check(path, exhaustive=True, max_cases=1000)`
and `tlaw.check_folder(path, exhaustive=True, max_cases=1000)`. The default limit
is 1,000 Cartesian combinations per law, or per pytest item when the law is
parametrized. Folder checks apply that limit separately; it is not an aggregate
execution budget. `--max-cases` requires `--exhaustive` and a positive integer.
Exhaustive checking, randomized search, and replay are separate modes.

Supported declarations are `bool`, `None`/`NoneType`, `typing.Literal`,
`tlaw.integer(low, high)`, `tlaw.one_of(values)`, bounded `tlaw.list_of`
over supported finite elements, and dictionary-form `tlaw.record` over finite
independent fields. PEP 695 aliases and the existing `Annotated[type, domain]`
form work too, with the same base/domain
validation. Explicit `@tlaw.law("description", value=Domain)` declarations work
and can leave other parameters to pytest fixtures or parametrization. Raw
Hypothesis strategies, bare `int`/`str`, floating ranges, unbounded lists,
constructor-based records, and `depends` are not enumerated. Unsupported
declarations or an oversized product fail during collection, before any law body
runs, with no sampling fallback.

Finite choices must contain replay-safe built-in values: `None`, booleans,
integers, floats, strings, bytes, and acyclic built-in containers of those values.
Enums, subclasses of built-ins, and arbitrary objects are refused because the
existing replay encoding cannot retain their exact types. A combination also
cannot contain shared container references, within or across its generated
parameters: replay retains values rather than object identities. Equal,
independent containers and references confined to mutually exclusive choices
remain supported. Choices preserve
their declared order and duplicates, including type-distinct `False` and `0`;
counts describe declaration entries rather than deduplicated values. A law with
no generated parameters has one combination.

### Bounded lists

`list_of(element, min_size=0, max_size=N)` enumerates lists in ascending length
order, then in the element declaration's Cartesian order. For an element domain
with `m` entries, its case count is `sum(m**k for k in range(min_size, N + 1))`.
Duplicate and type-distinct element entries count separately, just as in scalar
choices. The empty list contributes one case when `min_size=0`.

```python
Move = tlaw.one_of(["left", "right"])
Moves = tlaw.list_of(Move, max_size=4)


@tlaw.law
def reversing_twice_preserves_moves(moves: Moves):
    return list(reversed(list(reversed(moves)))) == moves
```

Running `tlaw check laws.py --exhaustive` reports:

```text
exhaustive: laws.py::reversing_twice_preserves_moves; 31/31 cases checked; lengths: moves=0..4; all declared cases passed
```

JSON includes `"list_bounds": {"moves": {"min_size": 0, "max_size": 4}}` on
the obligation. Nested lists work recursively: boolean lists of lengths `0..1`
have three values, so lists of those values with lengths `0..2` have 13 cases.
Their bounds appear under `moves` for the outer list and `moves[]` for each
inner list. Multiple generated parameters multiply their case counts.
Exhaustive folder output includes these per-law bounds and counts too, including
partial counts for laws that fail before enumeration finishes.

An explicit `max_size` and supported finite elements are required, even when
the only permitted list is empty. Existing `list_of` authoring still accepts
a tlaw domain or a concrete Python type as its element; use `one_of` for a
finite choice of literals. Cardinality checks saturate at the case cap before
enumeration, so huge powers or huge singleton ranges are not materialized.
Lengths above Python's `sys.maxsize` are refused. The case cap limits
combinations, not the memory or runtime of an individual input.

Generated list containers are fresh across positions and cases. Mutable values
declared through `one_of` keep their reference semantics: if a case would reuse
the same container in two positions, preflight refuses it under the existing
replay-safe reference rule. This also applies across generated parameters.

### Dictionary records

`record(name=domain, ...)` enumerates a dictionary for every combination of its
independent finite fields. Its count is the product of the field counts.
Fields keep their declaration order; the last field varies fastest. Records
can nest, contain bounded lists, and be elements of bounded lists.

```python
Command = tlaw.record(
    kind=tlaw.one_of(["credit", "debit"]),
    amount=tlaw.integer(0, 2),
)
History = tlaw.list_of(Command, max_size=2)


@tlaw.law
def command_amounts_stay_bounded(history: History):
    return all(0 <= command["amount"] <= 2 for command in history)
```

There are six commands, so this law enumerates `1 + 6 + 36 = 43` histories:

```text
exhaustive: laws.py::command_amounts_stay_bounded; 43/43 cases checked; lengths: history=0..2; all declared cases passed
```

Nested list bounds use field paths, for example `request.options.flags` and
`history[].options.flags`. Non-identifier field names use JSON bracket notation:
`request["options.flags"]` is distinct from `request.options.flags`. The same
paths appear in normal output, exhaustive folder output, and JSON `list_bounds`.
`--explain` also shows the declared field domains, including integer bounds and
finite choices. Records without lists have an empty `list_bounds` mapping and
still report exact case counts.

Only dictionary-form records with independent supported finite fields are
enumerated. Constructor-based records and dependent fields are refused before
law bodies run, including when nested inside an empty-only list. Existing
sampled support for those declarations is unchanged. Field declarations are
retained explicitly; enumeration does not inspect Hypothesis strategies.

The existing cap is checked against the complete product before dictionaries
or lists are enumerated. Generated dictionaries and nested generated containers
are independent across fields, positions, and cases. Declared `one_of` leaves
retain the same replay-safe reference restrictions as other finite domains;
sharing a mutable leaf across fields or positions is refused. Duplicate and
type-distinct field choices remain separate cases. The first failure retains
the original typed dictionary or history for exact replay through the existing
codec. Mutation can use the same finite declarations with `--exhaustive`.

### Execution and evidence

Declarations are snapshotted at collection, and each combination gets fresh
copies. The inferred form retains its input-mutation guard; explicit laws retain
their existing permission to mutate inputs. The first failing combination stops
that law and is retained through the same typed counterexample and comparison
machinery as sampled checks. Exhaustive failures are not shrunk. `assume()` and
`reject()` are refused as execution errors: an exhaustive run cannot discard
inputs. Hypothesis generation settings do not control enumeration.

Each obligation records `method`, `cases_checked`, `cases_total`, `exhausted`, and
`list_bounds`. Bounds describe declared exhaustive list domains; the mapping is
empty for scalar-only, sampled, and replay obligations. Replay counts retained
inputs and does not claim to enumerate the original list domain.

| Method | Counts and completion |
| --- | --- |
| `sampled` | Both counts are `null`; `exhausted` is false even if Hypothesis happens to visit every finite value. |
| `exhaustive` | Counts are attempted and declared combinations; a failing attempt counts, and `exhausted` is true only after every combination and pytest setup/teardown pass. |
| `replay` | Counts are attempted and retained inputs; `exhausted` is always false. |

Skip, xfail, setup/teardown errors, and interrupted execution cannot establish
exhaustion. An exhausted result establishes the observed law outcomes for those
declared inputs in that execution. It does not prove the law specification,
hidden state, external effects, or behavior outside the declared domain.
Mutation defaults to sampled checks and accepts `--exhaustive` to enumerate
these declared inputs in each ordinary run.

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
- each selected law case's source/function identity, pytest node ID, execution
  status, method, case counts, and exhaustion evidence in `obligations`;
- requested default, search, or exhaustive mode, each failure's generation posture,
  the search seed, and Hypothesis version;
- each failing law's module-path/function identity and source line;
- the final shrunk sampled inputs, or first failing exhaustive inputs, as typed JSON;
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
timeouts, incomplete law execution, or other harness failures. Existing v1
reports without `obligations` remain replayable: execution evidence is always
collected afresh, never trusted from the saved report.

### Execution diagnostics

`tlaw.check/v1` reports include a `diagnostics` array, separate from retained
counterexamples and law obligations. Each entry contains:

- `nodeid`: the pytest item or collector, or an empty string for a child-process
  outcome that cannot be attributed to one item;
- `identity`: the portable law identity, or `null` for collection, ordinary
  tests and process-level outcomes;
- `phase`: `collect`, `setup`, `call`, `teardown`, `run` or `receipt`;
- `exception_type`: the fully qualified type, or `null` when pytest provides
  only an outcome such as an unexpected pass;
- `message`: the exception message or pytest outcome explanation; and
- `detail`: pytest's formatted exception detail, retaining chained and grouped
  causes. It may contain the original absolute traceback paths.

For example, a passing law body can exceed Hypothesis's per-example deadline.
That run remains invalid, with no retained law counterexample, while its
diagnostic names `hypothesis.errors.DeadlineExceeded`. A `FlakyFailure` retains
its underlying exceptions in `detail`. Collection, fixture, skipped and xfailed
outcomes are also explained. Ordinary stable law failures keep their typed
counterexamples and exact replay alongside the diagnostic.

Diagnostics are collected at finalized pytest outcomes and written once when
the session finishes, in a separate receipt under the existing temporary
workspace. They are not a per-example trace or a record of backend session
state. A child timeout records `subprocess.TimeoutExpired` even when pytest
cannot finish its receipts. Missing or malformed diagnostic receipts produce
a `receipt` diagnostic; independent obligations and retained failures still
decide the verdict. Diagnostic messages alone cannot refute a law or confirm
a mutation kill.

Replay returns diagnostics from its fresh execution, replacing the original
run's diagnostics while preserving the retained failure history. Existing v1
reports without diagnostics remain replayable.

Replay retains skip, xfail, and fixture policies. Function-level pytest
parametrization is removed for replay because saved inputs already supply its
values; both decorator orders are tested. Inherited module/class parametrization
is not covered by this support. A replay result keeps the report's historical
summary and mode, with fresh `replay` obligation evidence for the current run.

### Law obligations

`check` accounts for each selected, collected tlaw law case. Parametrized cases
have separate pytest node IDs even when they share a source/function identity.
Ordinary pytest tests still run during a check, but do not count as laws.

Each obligation has one status:

| Status | Meaning |
| --- | --- |
| `passed` | Setup, the law call, and teardown complete successfully. |
| `failed` | The law call fails, with setup and teardown complete. |
| `skipped` | Pytest skips setup, the law call, or teardown. |
| `xfailed` | Pytest treats a failure or an unrun case as expected. |
| `xpassed` | An expected-failure case passes, including strict XPASS. |
| `error` | Setup or teardown fails. |
| `not_run` | Execution stops before all three phases are recorded. |

Only `passed` and `failed` can be completed obligations; finite passes also need
all required cases checked, and exhaustive passes need exhaustion. A successful check needs
at least one law, every obligation completed, no refutations, and a successful
pytest run. Skipped, expected-failure, and unrun laws cannot satisfy this gate.
The output states how many obligations completed and names every incomplete
case. Missing or malformed execution evidence also prevents success.
`--json` retains incomplete execution reports when they contain law obligations;
the command still exits `2`. Invalid runs with no law accounting do not produce
a JSON artifact.

Check exits `0` for a successful check, `1` for a refutation with complete
execution, and `2` for incomplete or invalid execution. A retained refutation
remains in `failures` and keeps `summary.status` as `failed` even if another
obligation is incomplete and the command exits `2`. This keeps the observed
defect separate from the incomplete run.

Replay requires every retained law identity to be collected and every selected
case to complete. Its successful calls mean the retained exception fingerprints
matched, rather than that the original laws now pass. A skipped replay cannot
claim a counterexample was reproduced. The replay result preserves the original
failure report and adds fresh `obligations`; its exit code describes the current
replay, while the retained summary describes the original check.

This inventory covers selected, collected laws, not code without laws or laws
deleted from the source. `tlaw diff` and repository review continue to make
contract changes visible. Mutation testing uses the same law-obligation and
typed counterexample machinery inside its isolated execution boundary.

Typed input encoding supports `None`, booleans, integers (without JSON's
integer-size ambiguity), finite or non-finite floats, strings, bytes, lists,
tuples, dictionaries, sets, and frozensets, recursively.

A counterexample containing another type — anything `record(Constructor, ...)`
builds, for instance — is a real law failure and is reported as one. These are
two separate facts and neither is allowed to overwrite the other:

- the law was refuted, so `summary.status` is `failed` and `check` exits `1`;
  and
- the counterexample could not be encoded, so `summary.retained` is `false`,
  the named input appears in the output, and no JSON is written.

Only when evidence was actually requested does the unretainable counterexample
decide the exit code: `check --json` exits `2`, because the artifact asked for
cannot be produced. A plain `check` in a CI gate therefore reports a genuine
defect as a defect rather than as a broken harness.

Law paths are relative when the law lives below the directory where `check`
was launched. Replay first resolves such a path from the current directory,
then from the retained report's directory. A report kept at a checkout root
therefore replays from that checkout even when the command is launched
elsewhere.

## Visible input domains

Ordinary annotations such as `int`, `str`, and `list[int]` use Hypothesis's
type inference. Seven concrete tlaw domain builders are also implemented —
`number`, `integer`, `one_of`, `list_of`, `recursive`, `record`, and
`depends`:

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
An integer bound must also be exactly representable as a finite float; otherwise
including that displayed bound is impossible and silently rounding it would
widen the visible domain.

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

`recursive(base, extend, min_leaves=None, max_leaves=100)` generates finite
recursive values. `base` is a domain or plain type. The extension accepts
exactly one positional child domain and must return a domain whose visible
description contains that child and whose strategy composes it through a tlaw
domain builder. An ignored child, description-only reference, non-domain
result, or shape that changes between calls is rejected rather than presented
as a recursive contract:

```python
Tree = tlaw.recursive(
    tlaw.integer(0, 9),
    lambda child: tlaw.list_of(child, max_size=3),
    min_leaves=1,
    max_leaves=20,
)
```

The leaf bounds are positive integers, with `min_leaves <= max_leaves`, and
count generated base values rather than depth. `--explain` renders the example
as `recursive(integer(0, 9), extend=list_of(self, max_size=3), min_leaves=1,
max_leaves=20)`. The generated-type declaration is the union of the provable
base and extension types; when either is unknown, tlaw makes no type claim.

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

### Composite domains

`record(**fields)` generates a value with fixed named fields, each drawn from
its own domain. Without a constructor it generates a dictionary; with one, the
constructor is applied to the drawn fields by keyword:

```python
Source = tlaw.record(attachment_id=tlaw.one_of(["upload-1"]))
Document = tlaw.record(
    DocumentRequest,
    id=DocumentIds,
    source=Source,
    fields=Selections,
)
Request = tlaw.record(documents=tlaw.list_of(Document, min_size=1, max_size=2))
```

Records nest through each other and through `list_of`, so a whole structured
request is one declaration, and `--explain` prints the complete generated
shape rather than an opaque name.

Each field is a tlaw domain or a plain type, exactly as `list_of` accepts
them. A constant field is stated as `one_of([value])`; a bare value is
refused, so a value is never mistaken for a type. A record with no fields is
refused, and a non-callable constructor is refused, both at definition time.

A field may also be a `depends(...)` domain, and inside a record it names
**sibling fields of that same record**:

```python
Document = tlaw.record(
    fields=Selections,
    checked=tlaw.depends(lambda fields: tlaw.one_of(fields)),
)
```

Siblings are the only namespace a record's `depends` can reach. A name that
is not a sibling is refused when the record is defined, and the refusal says
so — including when that name really is a law parameter. `depends` at law
level names law parameters; resolving one word against whichever namespace
happened to contain the name would be a guess about which contract the author
meant, so the boundary is enforced rather than inferred. Fields are drawn in
dependency order and cycles are refused, naming the record.

As at law level, the relation holds **by construction**: nothing is filtered,
so no example is discarded and no law using the record can quietly become
vacuous.

When the constructor is a class, the record declares that class as what it
generates, so a contradicting `Annotated` base type is refused like any other
domain. Any other callable declares nothing, and tlaw makes no claim.

A record **without** a constructor generates dictionaries, which the typed
input encoder retains, so its counterexamples replay. A record **with** a
constructor generates values of that type, and the encoder has no safe
representation for an arbitrary type: such a failure is still reported in
full, but it is not replayable evidence, so `check` exits `2` and `--json`
is not written. This is the pre-existing typed-encoding rule, and `record`
makes it easy to reach — prefer the dictionary form when replayable
evidence matters.

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
domains, dependent domains (`tlaw.depends(...)`), or Hypothesis strategies
directly, and `Annotated[T, tlaw.depends(...)]` is supported alongside
`Annotated[T, domain]` with the same runtime base-type compatibility check.
The explicit form deliberately follows
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
- dataclasses of the same type compare field by field, so tolerances apply
  inside dataclass fields and differences name the field path (such as
  `value.point.x`);
- recursive list, dictionary, and dataclass graphs use pair memoization:
  revisiting the same left/right container pair establishes that
  already-observed relation, while differences on finite paths beside a cycle
  are still reported;
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
other `Exception` becomes that evidence.

The expected type may be any exception type, including one outside
`Exception`. A non-`Exception` exit the law did **not** name still
propagates, so a law about `ValueError` never swallows `KeyboardInterrupt`.
Naming `asyncio.CancelledError` is the case that earned this: cancellation is
control flow, and the anef probe's cancellation law had no spelling before.

An `async` target is awaited. Calling an `async def` only builds a coroutine,
so the contract used to observe a returned coroutine instead of the outcome —
the work had not happened yet:

```python
@tlaw.law
def resolver_cancellation_propagates(declared: Selections):
    return tlaw.raises(asyncio.CancelledError, resolve, request, resolver)
```

The report says whether tlaw awaited: "the awaited call raised
CancelledError as required". tlaw owns a fresh event loop per observed call.
If a loop is already running in the thread, tlaw refuses with a harness error
rather than capturing that refusal as evidence about the code under law.

## One returned-branch contract

`tlaw.returns(Type, function, *arguments)` is the dual of `raises` for code
that returns its failure instead of raising it:

```python
@tlaw.law
def an_unknown_field_stays_caller_attributable(unknown: UnknownFieldIds):
    return tlaw.returns(Err, resolve, request_for(unknown), resolver).and_then(
        lambda failure: tlaw.same(caller_attributable(failure), True)
    )
```

A subclass counts, matching `isinstance`. An `async` target is awaited on the
same terms as `raises`. An exception is reported as the failure of the
contract — "expected Err, but the call raised TypeError: ..." — while a
non-`Exception` exit propagates. An exception type passed as the expected
branch is refused at the call site, naming `tlaw.raises` instead.

`Refusal.and_then(...)` and `Outcome.and_then(...)` state a second claim
about the exception that was raised or the value that came back. Combining
two claims with `and` converts the first to a boolean and loses its
explanation; `and_then` keeps whichever claim actually failed, so the report
either names the wrong branch or explains the second comparison, never both
collapsed into `False`.

`Comparison.and_then(...)` applies the same short-circuiting rule to
relationship claims. It takes a zero-argument callback: a comparison relates
two values rather than producing one distinguished payload, so a following
claim closes over whichever values it needs. This is the report-preserving
form for several comparisons and for an equivalence law that must also prove
the two implementations did meaningful work:

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

If the outputs differ, the first comparison is returned unchanged and the
length claim is not evaluated. If they agree but are both wrongly empty, the
second comparison reports the incorrect lengths instead of a bare `False`.

## One non-leakage contract

`tlaw.excludes(value, *fragments)` states that no forbidden fragment appears
anywhere in a value:

```python
return tlaw.returns(Err, resolve, request, exploding_resolver).and_then(
    lambda failure: tlaw.excludes(failure, hostname, "Traceback")
)
```

The search is structural. Dictionaries are searched through both keys and
values, because a leaked identifier used as a key is exactly as exposed as
one used as a value; lists, tuples, and sets are searched item by item; a
dataclass is descended into by declared field name, so a failure reports
`found at value.error.issues[0]` rather than one repr of the whole structure;
and an enum is searched through its name and value.

Recursive containers and dataclasses are visited once by identity, so a clean
cycle terminates and a leak elsewhere on a finite reachable path is still
located. The same forbidden fragments apply for the entire traversal, making
revisiting an object unnecessary.

A value whose content tlaw cannot establish is **refused**, naming the path
it was reached at. tlaw reads text, bytes, the primitives whose `repr` the
language itself defines, and containers, dataclasses, and enums built from
those. It does not render an arbitrary object with `repr` and search the
text, because a masking `repr` — Pydantic's `SecretStr('**********')`, or any
hand-written one that omits detail — would make this contract report "clean"
about a value that leaks as soon as it is serialized. A redaction law that
passes for that reason is worse than no law, so the guess is refused. The
refusal names the fix: pass the value as it leaves the system, which is also
the more accurate claim, since a client receives the serialized reply and
never a `repr`.

A `bytes` fragment is looked for in `bytes` and a `str` fragment in `str`;
tlaw does not guess an encoding to bridge them. An empty fragment, a
non-string fragment, and a call with no fragments at all are refused, because
each would make the law unfalsifiable. `Exclusion.and_then(...)` chains a
zero-argument follow-up claim on the same short-circuiting terms as
`Comparison.and_then(...)`, keeping the located leak report when a forbidden
fragment was found.

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
that run only, so the target repository is read, not modified. The target's
own pytest `addopts` are **not** applied: tlaw owns this invocation and
disables third-party plugin autoload, so an inherited flag from a plugin that
is now absent would turn every check into a pytest usage error. `--explain`
states this next to the plugin line. This is the
protocol recorded in `docs/kvfit-probe.md`. `tlaw mutate` uses the same
external laws-file pattern for package targets: it copies the target package
into an isolated run and places that copy before the installed package. Its
mandatory canary refuses to score the run if the laws did not actually
observe that copy.

## Commands and exit behavior

| Command | Implemented behavior | Exit behavior |
| --- | --- | --- |
| `tlaw check laws.py` | Runs one Python law file with third-party pytest plugin autoload disabled and the project's `addopts` not applied, and constructs replay evidence for a failure. The child runs in a temporary Hypothesis storage directory and writes no bytecode, so the checked tree is not modified. | `0` for pass, `1` for a law failure whether or not its counterexample could be retained, `2` when the harness could not produce a verdict at all. |
| `tlaw check feature/` | Discovers every `laws.py` and `*_laws.py` under a folder and checks each. Per-file evidence flags (`--search`, `--json`, `--seed`, `--replay`) are refused in folder mode. | Worst per-file code: `0` only when every file passes, else `1` or `2`. |
| `tlaw check laws.py --explain` | Adds the inferred-generation explanation before the pytest result. Aliased domains are expanded. | Same as `check`. |
| `tlaw check laws.py --search [--seed N]` | Uses a fresh visible seed, or reuses `N`, instead of deterministic inferred generation. | Same as `check`. |
| `tlaw check laws.py --exhaustive [--max-cases N]` | Enumerates supported finite inputs, with a default limit of 1,000 combinations per law case. Folder mode is supported. | Same as `check`; unsupported or oversized domains exit `2`. |
| `tlaw check laws.py --json report.json` | Writes the stable `tlaw.check/v1` report; it composes with `--search` or `--exhaustive`. | Same as `check`, except `2` when a counterexample cannot be retained, because the requested evidence cannot be produced. |
| `tlaw check --replay report.json` | Runs exactly the retained law inputs and no neighboring tests. | `0` when every failure is reproduced exactly, `1` when behavior differs, `2` for invalid evidence. |
| `tlaw diff BASE..HEAD` | Parses tlaw laws from two Git revisions without executing them and reports the law-space difference. | `0` for no law changes, `1` when law changes are present, `2` for an invalid range or unreadable source. |
| `tlaw audit audit.py` | Runs one declared baseline and declared known-wrong implementations from a Python `Lawbook`. Legacy TOML manifests remain supported but are deprecated: loading one emits a `DeprecationWarning` and the CLI prints a notice. | `0` when all declared wrong implementations are rejected, `1` when one survives, `2` for invalid evidence. |
| `tlaw audit audit.py --json report.json` | Also writes the stable `tlaw.audit/v1` JSON report. | Same as `audit`, except `2` when the requested report cannot be written. |
| `tlaw mutate laws.py implementation.py` | Generates deterministic, single-site mutations of one implementation module and runs the unchanged laws in isolated copies of the selected directory or package boundary. | `0` when every generated challenge is killed or explained, `1` when a survivor remains, `2` for no generated challenges, an invalid baseline, or harness errors. |
| `tlaw mutate ... --json report.json` | Also writes the stable `tlaw.mutation/v1` JSON report. | Same as `mutate`, except `2` when the requested report cannot be written. |

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
unmodified baseline must pass first, with at least one fully executed tlaw law
and every law obligation passed. Skips, expected failures, missing receipts,
and ordinary pytest tests alone cannot establish a valid baseline. Its measured
duration still determines each mutant's timeout budget.

Ordinary mutant runs must complete the same law inventory, including each
parametrized item. A survivor needs every law passed. An ordinary kill needs
retained typed inputs for the actual failed laws, followed by exact replay of
those inputs and exception fingerprints against the same frozen mutant in a
fresh copy. A neighboring pytest failure or setup/teardown error cannot supply
that law evidence. An unencodable counterexample remains visible as a
refutation, but prevents a confirmed ordinary kill. Collection failure under
a mutant remains invalid rather than a kill.

A calibrated timeout remains a candidate kill and must time out again under
a freshly recalibrated budget in another fresh copy. An ordinary failure on
confirmation cannot stand in for the timeout. Missing execution receipts on a timed-out
process remain unavailable; they are not counted as completed obligations.
Every unconfirmed candidate is an error.
The rejecting law is reported as a portable `laws.py::function_name` identity,
not as a path through tlaw's temporary mutation workspace.

Every kill is rerun once, and it is credited only when the replay returns the
same evidence rather than the same verdict. Each kill records a kind in the
report's `kill_kind` field: `observed` when laws reported failures, `timeout`
when the run exceeded its calibrated budget. A replay must reproduce that same
kind, and an `observed` kill must reproduce each retained law input with the
same exception fingerprint; a mismatch is an `error`, never a kill. A `timeout` kill re-measures the baseline
immediately before its replay and is re-timed against a budget recomputed from
that fresh measurement, so a machine that became busy after the run started
cannot turn two slow runs into a kill; if the re-measured baseline no longer
passes, the run is an `error` because no calibration remains. Only a confirmed
`observed` kill carries law names — a downgraded run keeps its first run's
attribution in its note instead.

Because a `timeout` kill names no law, the report counts those separately:
`summary.killed_by_timeout` in JSON, and a `Kills by calibrated timeout (no
law attribution)` line in the text report when any exist. They remain kills
and stay in the numerator; the separate count keeps that numerator
decomposable.

If the selected implementation contains no supported mutation sites, there is
nothing to challenge: the report keeps an empty mutation list, records
`summary.generated` as `0`, and reports invalid evidence with exit code `2`
instead of treating zero survivors as success. No canary is run in that case.


Before any mutant is scored, `mutate` runs a canary by default, with no
opt-out: a poisoned copy of the implementation in which every function
raises. If the laws still pass, they never observed the boundary module —
usually because they import an installed package rather than the sibling
file — and the run reports `INVALID EVIDENCE` with exit code `2` instead of
listing survivors that would mean nothing. The canary verdict appears in
both the text report and the JSON `canary` field.

For an ordinary canary result, the same complete inventory and an actual
captured law failure are required to establish wiring. The canary does not
need replayable inputs to show the poison was observed. Missing receipts,
fixture failures, or neighboring pytest failures leave wiring unverified.
The existing calibrated-timeout canary behavior is preserved.

The additive fields in `tlaw.mutation/v1` expose this evidence:

- `method` is `sampled` or `exhaustive`, and `max_cases` is the selected
  exhaustive cap or `null` for sampling;
- `baseline.obligations` and `baseline.evidence_error` account for the baseline;
- `canary.obligations`, `canary.evidence`, and `canary.evidence_error` describe
  the wiring observation;
- each mutation's `evidence` is its `tlaw.check/v1` report, with portable
  identities, obligations, and retained failures;
- `replay_evidence` contains fresh replay execution evidence when available;
- `replay_method` is `exact` or `timeout` when confirmation is attempted, and
  `null` otherwise; and
- `replay_exit_code`, `replay_timed_out`, and `replay_duration_seconds` record
  the physical confirmation attempt, even when no completed receipt exists.

For exact confirmation, a passed fresh replay receipt means the saved failure
reproduced; it does not mean the original law passed. `replay_method` records
the attempted method on both successful and unsuccessful confirmations; the
mutation outcome and note state whether the attempt confirmed a kill.

Exhaustive mutation is explicit:

```console
tlaw mutate laws.py implementation.py --exhaustive --max-cases 1000 --json mutations.json
```

The default cap is 1,000 combinations per collected law case, including each
parametrized item. The baseline, canary, mutants and timeout confirmation all
use the same mode and cap. Ordinary receipts must match the baseline's checking
method, total case counts and list length bounds, as well as its law inventory.
Passing exhaustive obligations must finish every case; failed obligations
record the attempted count before their first counterexample. Text and JSON
expose these counts and nested bounds. Unsupported or oversized domains
invalidate the baseline without falling back to sampling. Exact replay still
executes only retained inputs. See [mutation evidence](mutation-evidence.md#exhaustive-finite-inputs)
for the scope and limits.

Campaign planning and a shared run budget are explicit:

```console
tlaw mutate laws.py implementation.py --exhaustive --plan --json plan.json
tlaw mutate laws.py implementation.py --exhaustive --max-runs 20 --json mutations.json
```

`--plan` executes one isolated baseline, reports its inventory and finite
bounds/counts plus the campaign's estimated run range, and leaves the canary and
candidates unrun. It exits `0` for a valid requested plan without claiming
mutation evidence. The Python API uses `plan_only=True`.

`--max-runs` / `max_runs` limits isolated pytest invocations across the whole
campaign, including baseline, canary and every confirmation phase. It does not
limit elapsed time or memory and never clips calibrated deadlines. A budget
stop returns `2`, identifies the first blocked phase and mutant, and leaves
untouched candidates `not-run`. A candidate whose confirmation cannot run is
an `error`, with its original observation retained but no credited kill. Failed
baseline or canary prerequisites also leave candidates `not-run`.

JSON `plan` records selected-campaign estimates conditional on passing prerequisites;
sampled input totals remain `null`. `work` records the run cap, started and
remaining invocations, and `stopped_before`. `summary.not_run` and
`summary.complete` expose work left unrun and whether a campaign completed.
Plans always have `complete: false`. Unrun candidates stay outside the runnable
denominator, and an unrun equivalence declaration is unverified rather than
contradicted. See [planning and budgets](mutation-evidence.md#campaign-planning-and-run-budgets)
for the counting rules.

Use repeatable `--mutant ID` or Python `mutant_ids=[...]` to rerun exact
generated IDs in the current source. IDs must match in full; an empty or
unknown selection is rejected before child execution. Duplicate requests run
once in generation order. Each invocation gathers fresh isolated baseline,
canary and confirmation evidence under the existing budget and timeout rules.

Reports retain the whole inventory. Unselected entries are `excluded`, with
no execution evidence; `not-run` counts only selected mutants left untouched.
JSON mutation rows include `selected`, and summaries report `selected`,
`excluded`, `not_run`, `selection_complete` and whole-inventory `complete`.
Selected success can exit `0` with `selection_complete: true` while
`complete: false`; finishing a selection does not finish the full campaign.
Run/case estimates and strength denominators cover only the selection.
Excluded ledger entries remain unverified, while absent IDs remain stale.
This reruns a current mutation, without restoring historical source or
reusing evidence. See [exact mutant-ID reruns](mutation-evidence.md#exact-mutant-id-reruns).

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

The top-level package exports `law`, `system`, `same`, `raises`, `returns`,
`excludes`, `number`,
`integer`, `one_of`, `list_of`, `recursive`, `record`, `depends`, `check`,
`check_folder`,
`discover_law_files`, `replay`, `diff`, `audit`, and `mutate`, plus their result
and error types, including `FolderCheckResult` and `CampaignPlan`. Runner
functions return structured objects with text and JSON renderers where applicable.
`AuditResult.enforce()`
raises `tlaw.EnforcementError` unless the declared audit is sufficient.
Mutation results deliberately have no broad "correct" or "covered" verdict;
they report the generated challenges that were killed, survived, or could not
produce evidence.

## Not implemented

This is the single canonical list of tlaw's non-goals and unimplemented
surface. [Interface direction](interface-direction.md) and the
[README](../README.md) link here instead of keeping parallel lists that could
diverge. Nothing below is promised by the current version.

- arbitrary or composable domain predicates beyond the implemented numeric,
  finite-choice, list, recursive, record, and dependent domains;
- a relation reaching *across* scopes — a record field cannot depend on a law
  parameter, and a law parameter cannot depend on a record's field; each
  `depends` sees one namespace and says so when a name is not in it;
- a domain written directly inside a `dict[...]`, `tuple[...]`, or another
  annotation position tlaw cannot generate from; composite values are
  declared with `record` instead, and an unsupported position is still
  refused at decoration time rather than silently discarded;
- observing whether a collaborator was *not* called. tlaw sequences the calls
  a law makes; it does not spy. State effect-absence by injecting a double
  that signals when it is reached, and signal with a `BaseException` subclass
  when the code under law catches `Exception` broadly — a probe found a
  correct `except Exception` in the target absorbing a double's
  `AssertionError`, which turned a real violation into an unrelated failure
  (probes/anef);
- external effects and mutable state that a law does not represent as explicit
  paths: paths model the calls a law makes, not I/O, globals, or time;
- configurable NaN equality;
- opt-in loading of third-party pytest plugins;
- automatic pipeline tracing, dependency maps, contract-conflict planning, or
  project-wide graphs;
- existing pipelines whose steps are not composed explicitly in tlaw;
- a map of code with no laws, so `check` cannot report project-wide unknown
  behavior; and
- mutation across more than one implementation module at a time or across
  non-Python boundaries (one module at a time is supported, across flat,
  regular-package, and namespace-package boundaries; compiled modules are
  refused because a source-level tool cannot mutate them).

Neither writing nor enforcing a law requires an LLM or network access. Python
law files, Python audit files, and the application code they import are
executable code and should only be run when trusted.
