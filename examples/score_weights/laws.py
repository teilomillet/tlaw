"""Laws for score normalization, written before the implementation.

The import below is `from implementation import ...` — the module's own
name, not a package path. That is what keeps this folder compatible with
`tlaw mutate`: the mutation boundary is this directory, and the laws must
observe the copy inside it. The canary enforces this; laws that import an
installed package instead are reported as INVALID EVIDENCE, not as
survivors.
"""

import tlaw

from implementation import normalize_scores


Scores = tlaw.list_of(tlaw.number(-10, 10), min_size=1, max_size=8)
Shift = tlaw.number(-5, 5)


@tlaw.law
def weights_sum_to_one(scores: Scores):
    return tlaw.same(sum(normalize_scores(scores)), 1.0, within=1e-9)


@tlaw.law
def weights_stay_between_zero_and_one(scores: Scores):
    return all(0 <= weight <= 1 for weight in normalize_scores(scores))


@tlaw.law
def the_largest_score_gets_the_largest_weight(scores: Scores):
    weights = normalize_scores(scores)
    return weights[scores.index(max(scores))] == max(weights)


@tlaw.law
def shifting_every_score_changes_nothing(scores: Scores, shift: Shift):
    shifted = [score + shift for score in scores]
    return tlaw.same(
        normalize_scores(shifted),
        normalize_scores(scores),
        within=1e-9,
    )
