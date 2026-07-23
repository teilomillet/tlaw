# tlaw interface direction

Status: working product direction. The beginner form, visible domains,
`tlaw.same`, and the first generated-mutation boundary are implemented. See
[current tlaw behavior](current-behavior.md) for executable facts; this document
also contains future direction.

The implemented surface is pinned by executable tests and summarized in
[current tlaw behavior](current-behavior.md).

## The source of truth

A tlaw file is executable code. It is not prose that a person or language model
must interpret. The same file is the source from which tlaw runs generated
checks and, later, derives documentation, dependency views, pipeline graphs,
and change-impact reports.

An LLM may propose changes to a law file, just as a human developer may, but it
is never required to write, interpret, compile, or enforce one.

A law committed to the repository is accepted. Git is the acceptance ledger;
tlaw must not add a second approval registry or hidden state. The implementation
may change freely while the committed laws continue to pass. When intended
behavior changes, the executable law changes in the commit and its diff is
reviewed as code.

The resulting Git and CI trust model is detailed in
[acceptance and evidence](acceptance-and-evidence.md).

## Evidence before derived views

The beginner form intentionally makes laws cheap to write, including weak laws
such as checking only a return type or container length. Passing such a law is
not strong evidence by itself.

Generated mutations of the current implementation are therefore load-bearing,
not an optional audit feature. The first boundary now exists as
`tlaw mutate laws.py implementation.py`: it challenges laws with deterministic
single-site changes that the law author did not choose and reports every
survivor. tlaw still must not turn that narrow evidence into a broad claim or a
confident-looking project graph.

The required outcome semantics and first mutation boundary are specified in
[generated mutation evidence](mutation-evidence.md).

The development order is:

1. keep the executable authoring form small — implemented;
2. make generated inputs and comparison semantics visible — implemented for
   `--explain`, `number`, `integer`, `one_of`, `list_of`, `depends`, and
   absolute/relative `same`; randomized search, retained reports, and exact
   replay are implemented;
3. challenge accepted laws with generated mutations — flat modules, regular
   package boundaries, and namespace-package boundaries are implemented; and
   only then
4. build derived project-wide views whose displayed confidence comes from that
   evidence — not implemented.

## First-use standard

Someone who understands a Python function, a list, and `==` should be able to
write and run a first law. They should not first need to understand Hypothesis,
pytest, property-based testing, mutation testing, or category theory.

The first interface is ordinary Python:

```python
import tlaw

from implementation import reverse


@tlaw.law
def reversing_twice_returns_the_original(values: list[int]):
    return reverse(reverse(values)) == values
```

Run it with:

```console
tlaw check laws.py
```

Use `tlaw check laws.py --explain` to show the inferred domain, Hypothesis
version, example budget, and deterministic replay posture.

This small program is the law:

- the function name states the behavior;
- the parameter annotation states the kind of generated input;
- the body calls the real implementation; and
- the returned boolean is the condition that must always hold.

tlaw asks Hypothesis to generate values from the annotation. If the expression
can be false, Hypothesis searches for and shrinks a counterexample. The author
does not write or maintain a second generated test file.

The bare form assumes pure behavior. tlaw copies generated inputs and rejects a
passing law when it can detect that an input was mutated. It errors when it
cannot copy or compare an input safely. This guard detects persistent changes;
it cannot prove the absence of mutation that is later reversed or of effects on
external state. Explicit stateful laws remain necessary for those cases.

Bare laws use a fixed example budget with deterministic generation and no local
example database. Their minimized failing input is part of the check output.
`tlaw check --json` retains typed inputs and the Hypothesis version;
`tlaw check --replay REPORT` executes only those exact counterexamples and
requires the same failure fingerprint. `--search` chooses and prints a random
seed, while `--search --seed N` repeats that discovery sequence.

## Implemented escalation when types and `==` are insufficient

The first law stays small. A law that genuinely needs a narrower input domain
may define it beside the law:

```python
Reward = tlaw.number(0, 1)
Rewards = tlaw.list_of(Reward, min_size=1)
```

`number` generates finite floats between inclusive bounds, `integer` generates
bounded counts, `one_of` states a finite set, `list_of` composes a domain or
ordinary type with visible size bounds, and `depends` derives a domain from
already drawn parameters. Their descriptions appear in `--explain`.

A law that needs retained values, nested comparison, arrays, or float tolerance
may return:

```python
return tlaw.same(actual, expected, within=1e-12, relative=1e-9)
```

`within` is absolute tolerance near zero; `relative` scales with the larger
magnitude. Both default to zero, so exact comparison remains the default.
`same` retains both values and the first difference, compares array shapes,
and normalizes array-like values through `tolist()` without depending on NumPy.

A bare domain annotation is a runtime value, so strict type checkers may
flag `rewards: Rewards` as an invalid type expression. The checker-clean
spelling wraps the domain in `Annotated` — the base type is for the reader
and the checker, tlaw enforces the domain — and works through PEP 695
aliases and inside `list[...]`:

```python
from typing import Annotated


type Reward = Annotated[float, tlaw.number(0, 1)]


@tlaw.law
def rewards_stay_bounded(rewards: list[Reward]):
    return all(0 <= reward <= 1 for reward in rewards)
```

Exactly one domain per `Annotated` value is enforced; foreign metadata, and
domains in positions tlaw cannot generate from yet, are rejected at
decoration time rather than silently erased. tlaw refuses a base type and
domain that it can prove disagree; when Python does not expose enough runtime
type information to prove the relationship, tlaw leaves it unproven rather
than guessing. `--explain` expands supported PEP 695 aliases so the generated
domain remains visible.

## Interface budget

The beginner path should require only two new ideas:

- `@tlaw.law` means "try this with many generated inputs"; and
- returning `left == right` means the relationship must always hold.

Descriptions should come from function names. Input generation should come
from ordinary type annotations when that is unambiguous. `number`, `integer`,
`one_of`, `list_of`, and `depends` are the implemented escape hatches paid for
by real domain bottlenecks; any additional vocabulary must pay for its
complexity with another demonstrated example.

The existing path, observation, explicit Hypothesis strategy, and audit APIs
remain useful experiments and escape hatches. They are not prerequisites for a
person's first law.

## Larger codebases

The eventual goal is executable behavioral structure at more than one scale:

- a local law constrains one stable code boundary;
- a connection law constrains how two boundaries fit together; and
- a whole-flow law constrains behavior that only exists across a pipeline.

These laws should call and compose real code. A diagram or contract card is a
derived view, never a competing source of truth. A code change should be
traceable to the laws it intends to add, modify, or preserve.

Existing behavior is not automatically correct. It becomes accepted behavior
only when it is written as an executable law and committed. Missing laws must
remain visible as unknown rather than being reported as safe.

## Locality is part of the contract

A person given one feature folder should be able to understand what that part
of the program does, what it depends on, and which behavior it promises without
searching the whole repository. Its implementation and executable laws should
therefore live together.

A preferred feature shape is intentionally ordinary:

```text
feature/
├── implementation.py
└── laws.py
```

Larger folders may contain several clearly named implementation and law files,
but tlaw should not require a distant contract registry, generated shadow
source, or hidden project configuration to give those files meaning.

The code itself should favor:

- short functions with one visible responsibility;
- values returned explicitly instead of state changed at a distance;
- pure functions wherever the domain permits them;
- imports from modules named after real domain concepts;
- configuration passed or declared where it affects behavior; and
- a top-to-bottom reading order that exposes the important flow.

Helpers whose purpose is to hide part of one function's body should be close
to zero. Splitting a flow into private fragments does not simplify it; it
spreads one behavior across the file and makes it harder to reflect on. The
two honest homes for code are:

- code genuinely reused by other parts of the program moves to an explicit
  shared module such as `utils`, where reuse is the declared purpose and the
  code receives a clear name, a narrow interface, and its own colocated
  laws; and
- everything else stays functional: segment the flow into real steps — each
  a visible function from values to values — and let reuse come from
  composing those steps, never from extracting private fragments.

This is the same shape as tlaw's own paths: named steps composed in the
open, so the whole flow reads top to bottom and each step can carry its own
law.

This is not a prohibition on dependencies. Cross-folder behavior is sometimes
the point of a pipeline. Those dependencies must be explicit, directional, and
named so a reader can follow them. tlaw should derive larger flow views from
these local executable relationships rather than moving their truth into a
central file.

The readability target is a person learning Python at roughly fifteen years
old. They may need time to understand the domain, but framework ceremony,
indirection, clever abstractions, and aesthetic-only helpers should not be what
blocks them.

## What remains deliberately unresolved

Since this document was first written, several items have earned syntax from
real probes: generated mutations for flat modules, regular packages, and
namespace packages (`tlaw mutate`, documented in
[generated mutation evidence](mutation-evidence.md)); numeric, integer,
finite-choice, list, and dependent domains; explicit exception contracts; and
tolerant nested comparison including array shapes (`tlaw.same(left, right,
within=..., relative=...)`). A checker-clean `Annotated[type, domain]` form is
also implemented, including PEP 695 aliases and `list[...]`; unrelated or
mixed metadata is rejected rather than ignored. Proven base/domain mismatches
are refused, alias bounds are expanded in `--explain`, and `tlaw check` can
discover law files under a folder.

The remaining interface is not settled for:

- mutation of more than one implementation module or non-Python boundaries;
- external effects and mutable state not represented by explicit paths;
- domain predicates beyond numeric ranges, finite choices, lists, and declared
  dependencies;
- domains nested inside dictionaries, tuples, custom classes, or other
  unsupported positions;
- configurable NaN equality;
- opt-in third-party pytest plugins;
- existing pipelines whose steps are not explicitly composed in tlaw;
- project-wide impact analysis and contract conflicts.

Those cases should be designed from real code one at a time. They must not add
ceremony to the beginner form when their complexity can remain behind an
adapter or an advanced interface.

## Acceptance test for every interface change

An interface change is an improvement only when it preserves executable truth
and makes a real law easier to read or write. In particular:

1. the law runs without an LLM or network access;
2. its meaning is carried by executable code rather than prose;
3. a beginner can alter the example without learning Hypothesis syntax;
4. failure produces a concrete generated counterexample;
5. generated views cannot silently change the law's meaning; and
6. unsupported behavior is reported honestly instead of guessed;
7. passing laws are challenged independently rather than self-grading their
   own strength; and
8. the same code and tool versions reproduce the same generated failure.
