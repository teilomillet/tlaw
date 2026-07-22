"""Small pure functions for reward values."""


def center(rewards: list[float]) -> list[float]:
    """Return rewards whose average is zero."""
    if not rewards:
        return []

    average = sum(rewards) / len(rewards)
    return [reward - average for reward in rewards]
