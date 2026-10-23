# Laws for tlaw itself

This suite exercises the real tlaw core and challenges its laws with ten
declared behavioral defects. It uses the existing authoring, audit, and replay
interfaces; it adds no production API.

Run it from the repository root:

```console
uv run tlaw check selflaws/
uv run tlaw audit selflaws/audit.py
uv run pytest -q tests/test_self_laws.py
```

`scripts/gate` runs all three paths, along with the existing example checks
and generated-mutation challenges. The full pytest run includes the independent
self-law tests; it does not recursively invoke the gate.

## What the laws state

| File | Property and independent expectation |
| --- | --- |
| `replay_laws.py` | Encoding and decoding preserve scalar types and values, nested lists/tuples/text-keyed dictionaries, integer sets and frozensets; integer wire values match explicit JSON fields. NaN remains NaN and signed zero keeps its sign. |
| `comparison_laws.py` | Numeric matching agrees with `math.isclose`; booleans remain distinct from integers; a changed nested leaf identifies its actual location and values. |
| `domains_laws.py` | Generated integers respect written bounds; lists respect written size bounds; dependent lengths fit the actual generated budget. |
| `paths_laws.py` | Composition executes every step in order, identity and grouping preserve the independently calculated result, and repeated path executions start fresh. |

The expectations use plain Python, literal wire fields, and `math.isclose`.
They never use `tlaw.same` to decide whether `tlaw.same` is correct. Codec
round trips are paired with wire-format expectations because matching encode
and decode errors can cancel each other.

`implementations.Core` selects the real functions through static aliases. Its
small mutable counter is a fixture for path construction: tlaw's audit target
also becomes the constructor used by a `System`. Each wrong subclass changes
one behavior. `audit.py` names each defect and the property it violates.

## Who checks the verdicts

The independent tests run the laws under ordinary pytest subprocesses and
inspect pytest's JUnit output. They require the real implementation to pass
every expected law and each wrong implementation to fail its named law with
an `AssertionError`. Import errors, collection errors, skips, or an unrelated
failure cannot stand in for the expected rejection. An explicit law-name
inventory also makes accidental omissions visible.

One test replaces the law decorator with an always-passing implementation and
requires the independent rejection check to fail. Another makes a scratch copy
of the codec suite with a wrong default implementation, retains its generated
counterexamples, replays their exact inputs and exception fingerprints, then
restores the real implementation and requires replay to report the repair.
The committed laws and production code are not changed by these tests.

## Evidence boundaries

These are generated tests and rejection evidence for a declared set of ten
defects, not a formal proof or a percentage of tlaw's correctness. Inferred
laws retain tlaw's deterministic budget of up to 100 examples; the explicit
strategy laws use a deterministic profile of up to 60 examples without an
example database. Container sizes and numeric domains are deliberately bounded
where the law states them.

The independent harness shares Python, pytest, Hypothesis, and the operating
system with tlaw. Its expectations are independent of tlaw's own verdicts,
not an independent runtime or proof kernel. Existing lower-level tests,
obligation-accounting regressions, and mutation checks remain necessary.
