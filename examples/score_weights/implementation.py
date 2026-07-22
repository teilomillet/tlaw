"""Turn raw model scores into positive weights that sum to one.

This implementation was written second: the laws in laws.py existed
first and failed until this code satisfied them. See README.md for the
law-first workflow this folder demonstrates.
"""

import math


def normalize_scores(scores):
    """Softmax with the standard max-shift for numerical stability."""
    largest = max(scores)
    shifted = [math.exp(score - largest) for score in scores]
    total = sum(shifted)
    return [value / total for value in shifted]
