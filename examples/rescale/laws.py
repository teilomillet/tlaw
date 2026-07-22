"""Laws for a numerical-stability refactor, spanning magnitudes.

`total_kahan` replaced `total_naive` to reduce rounding error. The two
agree mathematically, so the refactor must preserve results — but only
to relative precision, because at large magnitude a correct compensated
sum differs from the naive one by an absolute amount proportional to the
values. `relative=` states exactly that contract; a fixed `within=` here
would reject correct code at scale or pass wrong code near zero.
"""

import tlaw

from implementation import total_kahan, total_naive


# Magnitudes span nine orders, so no single absolute tolerance fits.
Values = tlaw.list_of(tlaw.number(-1_000_000, 1_000_000), min_size=1, max_size=50)


@tlaw.law
def kahan_matches_naive_to_relative_precision(values: Values):
    return tlaw.same(
        total_kahan(values),
        total_naive(values),
        relative=1e-9,
    )
