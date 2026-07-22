"""Laws for episode batch packing, written before the implementation.

The feature: pack sequences (given by token length) into batches that
respect a token budget, preserving order, greedily filling each batch.
Every spec decision is a law here, made before any code existed:

- the result is a tuple of tuples of *indices* into the input;
- order is preserved and every sequence is packed exactly once;
- no batch exceeds the budget, and none is empty;
- a batch closes only when the next sequence cannot fit (greedy);
- a bigger budget never produces more batches;
- packing nothing yields nothing; and
- a sequence longer than the budget is refused with ValueError.

Domain note: valid inputs are relational — no sequence may exceed the
budget — which `Lengths` states as a dependent domain. Every generated
example satisfies the relation by construction, so nothing is filtered,
no example is vacuous, and `--explain` describes exactly what runs.
"""

import tlaw

from implementation import pack


Budget = tlaw.integer(1, 30)
Extra = tlaw.integer(0, 30)

# Valid packing inputs are relational: no sequence may exceed the budget.
# A dependent domain states that where the law is read, and generates it
# by construction — so --explain describes exactly what runs.
Lengths = tlaw.depends(
    lambda budget: tlaw.list_of(tlaw.integer(1, budget), max_size=12)
)


@tlaw.law
def every_sequence_is_packed_exactly_once_in_order(budget: Budget, lengths: Lengths):
    flat = [index for batch in pack(lengths, budget) for index in batch]
    return flat == list(range(len(lengths)))


@tlaw.law
def no_batch_exceeds_the_budget(budget: Budget, lengths: Lengths):
    return all(
        sum(lengths[index] for index in batch) <= budget
        for batch in pack(lengths, budget)
    )


@tlaw.law
def batches_are_never_empty(budget: Budget, lengths: Lengths):
    return all(len(batch) >= 1 for batch in pack(lengths, budget))


@tlaw.law
def a_batch_closes_only_when_the_next_sequence_cannot_fit(
    budget: Budget, lengths: Lengths
):
    batches = pack(lengths, budget)
    return all(
        sum(lengths[index] for index in batches[position])
        + lengths[batches[position + 1][0]]
        > budget
        for position in range(len(batches) - 1)
    )


@tlaw.law
def a_bigger_budget_never_creates_more_batches(
    budget: Budget, lengths: Lengths, extra: Extra
):
    return len(pack(lengths, budget + extra)) <= len(pack(lengths, budget))


@tlaw.law
def packing_nothing_yields_nothing(budget: Budget):
    return pack([], budget) == ()


@tlaw.law
def an_oversized_sequence_is_refused(budget: Budget, lengths: Lengths):
    oversized = lengths + [budget + 1]
    return tlaw.raises(ValueError, pack, oversized, budget)
