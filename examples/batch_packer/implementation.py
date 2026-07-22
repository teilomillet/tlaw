"""Order-preserving batch packing under a token budget.

Written after laws.py was red. The laws are the spec: greedy first-fit
in order, indices out, ValueError for anything that can never fit.
"""


def pack(lengths, budget):
    """Pack sequence indices into batches whose lengths sum within budget."""
    if budget < 1:
        raise ValueError("budget must be a positive number of tokens")

    batches = []
    current = []
    used = 0
    for index, length in enumerate(lengths):
        if length < 1:
            raise ValueError(f"sequence {index} has non-positive length {length}")
        if length > budget:
            raise ValueError(
                f"sequence {index} needs {length} tokens; budget is {budget}"
            )
        if current and used + length > budget:
            batches.append(tuple(current))
            current = []
            used = 0
        current.append(index)
        used += length
    if current:
        batches.append(tuple(current))
    return tuple(batches)
