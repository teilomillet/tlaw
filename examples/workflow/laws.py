import tlaw

from implementation import increment


Count = tlaw.integer(0, 2)


@tlaw.law
def increment_advances_once(value: Count):
    return increment(value) == value + 1
