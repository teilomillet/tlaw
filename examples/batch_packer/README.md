# Batch packer: a feature driven by its laws

This folder is the record of a real law-first development run: the seven
laws in `laws.py` were written and run **before** `pack` existed, against a
stub that only raised. Every output below is from the actual session.

## The feature

Pack sequences (given by token length) into batches under a token budget:
order-preserving, greedy, indices out, `ValueError` for anything that can
never fit. The shape every RL trainer and inference server needs somewhere.

## The three commands

**1. Laws first — the spec must be able to fail:**

```console
$ tlaw check laws.py        # implementation is a NotImplementedError stub
7 failed
```

Writing the laws first forced every spec decision before any code: indices
rather than lengths in the output, tuples rather than lists, refusal rather
than silent dropping for oversized sequences, and — the decision that later
proved load-bearing — *greedy* packing stated as its own law: a batch
closes only when the next sequence cannot fit.

**2. Implement until green:**

```console
$ tlaw check laws.py
7 passed
```

One pass. With seven laws fixing the semantics, the implementation was
close to dictated.

**3. Grade the laws, not the code:**

```console
$ tlaw mutate laws.py implementation.py
Canary: KILLED (laws observe the mutated module)
Killed 13 of 13 runnable generated mutations.
Survivors: 0
```

## The line worth reading twice

```text
- 7e70dd29b1ef KILLED pack: replace used + length > budget with
  used + length >= budget (law: laws.py::a_batch_closes_only_when_the_next_sequence_cannot_fit)
```

The exact-fit boundary mutant — the classic off-by-one that ships to
production — was killed by **exactly one law**: the greedy maximality law.
Six other laws all passed it. The kill attribution is a responsibility
map: it shows which law is the sole defender of which behavior, and it
*proves* the subtle law earned its place instead of asserting it.
Likewise `used + length` → `used - length` was killed only by
`no_batch_exceeds_the_budget`.

## Friction found (recorded for the interface)

The run surfaced two real bottlenecks, written up in
[the law-first experiment record](../../docs/law-first-experiment.md), and
both were fixed the same day — this folder is the after picture.

The oversized-refusal law was first try/except boilerplate; it now reads

```python
return tlaw.raises(ValueError, pack, oversized, budget)
```

with failures that name the actual outcome instead of `False`.

Valid inputs are relational — no sequence may exceed the budget — which
the laws first handled by clamping inside each body, making `--explain`
describe a wider domain than the laws exercised. They now declare it:

```python
Lengths = tlaw.depends(
    lambda budget: tlaw.list_of(tlaw.integer(1, budget), max_size=12)
)
```

Every example satisfies the relation by construction, and `--explain`
reports `lengths: depends(budget)`. The laws still kill 13 of 13 mutants,
so the rewrite lost no strength.
