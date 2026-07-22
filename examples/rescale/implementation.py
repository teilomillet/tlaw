"""A numerical-stability refactor: naive accumulation to Kahan summation.

The two are mathematically equal. For well-conditioned inputs they agree
to a few parts in 10^15 — but at magnitude that agreement is an absolute
gap far larger than any fixed small `within`. This is the exact shape a
numerical or GPU refactor produces, and it is why `relative=` exists.
"""


def total_naive(values):
    total = 0.0
    for value in values:
        total += value
    return total


def total_kahan(values):
    """Compensated summation: same result, less accumulated rounding."""
    total = 0.0
    compensation = 0.0
    for value in values:
        adjusted = value - compensation
        stepped = total + adjusted
        compensation = (stepped - total) - adjusted
        total = stepped
    return total
