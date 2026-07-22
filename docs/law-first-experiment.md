# Law-first development experiment

Date: 2026-07-22

Status: third experiment type, after the two read-only probes (retrain,
kvfit). This one develops a **new feature from scratch, driven by its
laws**: `examples/batch_packer/`. All numbers below are from the real
session; the example folder reproduces them.

## Question under test

Can a feature of moderate, honest complexity — order-preserving greedy
batch packing under a token budget, with an error contract and relational
properties — be developed laws-first with the current interface, and does
the resulting evidence say anything a test suite would not?

## What happened

1. **Seven laws were written against a stub** that only raised. `tlaw
   check` reported 7 failed. The red phase is what makes the spec real: a
   law that has never failed has never demonstrated it can.
2. **The implementation passed on the first attempt.** This is a finding,
   not a boast: with the output shape, the greedy rule, the error
   contract, and the empty case all fixed as laws, the code was close to
   dictated. The design work happened in law space, where it is reviewable.
3. **`tlaw mutate`: canary killed, 13 of 13 runnable mutants killed, zero
   survivors, exit 0** — a complete evidence chain from three commands in
   under a minute.

## The strongest observation: attribution is a responsibility map

Most mutants died to five or six laws at once. Two died to exactly one:

- `used + length > budget` → `>= budget` (the exact-fit boundary) died
  **only** to `a_batch_closes_only_when_the_next_sequence_cannot_fit`;
- `used + length` → `used - length` died **only** to
  `no_batch_exceeds_the_budget`.

A law killed by many is redundant coverage; a mutant killed by one law
identifies that law as the sole defender of a behavior. Deleting the
greedy law would leave the classic off-by-one shippable, and the report
*shows* that instead of asserting it. This is evidence a conventional test
suite does not produce about itself.

### The blind spot in self-justifying laws

A later probe (`docs/package-boundary-probe.md`) found the limit of this
style, so it is recorded here beside the praise.

kvfit's strongest law states that every layout's verdict is justified by
*its own reported numbers*. That law is excellent at catching inconsistent
output — and structurally unable to catch a wrong **shared input**: when
the planner's memory budget was mutated from `memory_gib * GIB` to
`memory_gib / GIB`, every layout stayed internally consistent with its own
absurd budget, and the law passed.

The rule this yields:

> A law that compares output to output cannot detect an error in an input
> both sides share. Every quantity that enters more than one comparison
> needs at least one law anchored to a fact outside the implementation.

The anchor that killed it is one line of arithmetic the reader can check
without trusting the planner at all:

```python
expected = memory_gib * GIB * utilization
return all(
    tlaw.same(layout.budget_per_rank_bytes, expected, within=1e-6)
    for layout in plan(...)
)
```

Adding it took kvfit's planner from 16 to 17 of 40 mutants killed, and the
report names that law as the **sole** killer of the budget mutant. The
blind spot was in the law-writing pattern, not in one law — which is
exactly the kind of thing only generated mutation reveals.

## Verified bottlenecks

### 1. Dependent domains did not exist, so laws clamped inputs in their bodies

**Resolved the same day** by `tlaw.depends(builder)`. The batch-packer laws
now declare

```python
Lengths = tlaw.depends(
    lambda budget: tlaw.list_of(tlaw.integer(1, budget), max_size=12)
)
```

and the clamp is gone. `--explain` reports `lengths: depends(budget)`, which
is exact rather than wider than what runs, and the mutation run still kills
13 of 13. The design was taken from the hardest available case — retrain's
three-way linked shapes, where rewards, logprobs, and masks must agree on
episode and token counts — rather than from this easier two-parameter one.
`depends` supplies the dependency wiring, not every possible domain
vocabulary. At this stage, kvfit's divisor-only input set still needed an
explicit strategy; that finding later paid for the implemented
`tlaw.one_of(...)` finite-choice domain recorded in
[`package-boundary-probe.md`](package-boundary-probe.md).

The original finding is kept below, because the reasoning is what paid for
the feature.



Valid packing inputs satisfy a relation between two parameters:
`length <= budget`. No domain can express that, so every valid-input law
derives its real inputs inside its body (`fitted(raw, budget)` clamps).
This works and stays visible, but it has a truthfulness cost: `--explain`
reports the *raw* generated domain, while the law actually exercises the
clamped one — the visible contract slightly overstates. The related-inputs
bottleneck from the retrain probe (linked shapes across rewards, logprobs,
masks) is the same gap at larger scale. Two candidate directions to probe,
not design in advance: a derived-domain vocabulary, or an explicit
assume/precondition that reports how many examples made real claims —
silent vacuity would be worse than the current clamp.

### 2. Exception contracts are spelled as try/except

`an_oversized_sequence_is_refused` was first written as try/except
boilerplate around a one-idea claim ("this input raises ValueError"), and
when such a law failed the output said only that it returned False — not
whether the wrong exception, no exception, or the wrong outcome occurred.

Resolved the same day, with this example accepted as payment:
`tlaw.raises(ValueError, pack, lengths, budget)` performs the call,
returns a truthy `Refusal` when the expected exception (or a subclass)
was raised, and otherwise names the actual outcome in the failure
message. The law in `examples/batch_packer/laws.py` now uses it, and the
mutation run still kills 13 of 13.

### 3. The red phase is noisy but honest

Seven NotImplementedError tracebacks are more output than "7 laws await an
implementation." Cosmetic, low priority, recorded for completeness.

## Verdict on the workflow

For a feature of this size the loop is already better than test-after:
the spec decisions were forced into a reviewable artifact before code
existed, the implementation followed in one pass, and the mutation report
graded the spec itself — including proving which law is load-bearing.
The friction that exists is in input vocabulary (dependent domains), not
in the loop.
