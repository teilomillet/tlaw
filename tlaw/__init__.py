"""Declare visible domains and comparisons for executable behavioral laws."""

from __future__ import annotations

from .comparison import Comparison, same
from .domains import (
    DependentDomain,
    Domain,
    depends,
    integer,
    list_of,
    number,
    one_of,
)


__all__ = [
    "Comparison",
    "DependentDomain",
    "Domain",
    "depends",
    "integer",
    "list_of",
    "number",
    "one_of",
    "same",
]
