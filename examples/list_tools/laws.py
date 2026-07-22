import tlaw

from implementation import reverse


@tlaw.law
def reversing_twice_returns_the_original(values: list[int]) -> bool:
    return reverse(reverse(values)) == values
