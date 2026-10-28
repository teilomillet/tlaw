"""Declared challenges to the self-laws, separate from their expectations."""

from implementations import (
    AlwaysEqual,
    CollapsesTuples,
    Core,
    DropsComposedStep,
    IgnoresDataclassFields,
    IgnoresTolerance,
    LosesIntegerSign,
    PermitsEmptyLists,
    ReusesState,
    ShiftedWireIntegers,
    ShiftsDependency,
    SkipsMappingKeys,
    WidensIntegerDomain,
)
from tlaw.book import Lawbook


evidence = Lawbook(
    __file__,
    tests=(
        "replay_laws.py",
        "comparison_laws.py",
        "domains_laws.py",
        "paths_laws.py",
        "exclusion_laws.py",
    ),
)
evidence.baseline("core", Core)
evidence.known_wrong(
    "loses-integer-sign",
    LosesIntegerSign,
    breaks="Replay preserves signed integer values.",
)
evidence.known_wrong(
    "collapses-tuples", CollapsesTuples, breaks="Replay preserves tuple structure."
)
evidence.known_wrong(
    "shifted-wire-integers",
    ShiftedWireIntegers,
    breaks="The wire format stores the actual integer, even when two encoding errors cancel.",
)
evidence.known_wrong(
    "always-equal",
    AlwaysEqual,
    breaks="Comparison rejects values outside the declared tolerances.",
)
evidence.known_wrong(
    "ignores-tolerance",
    IgnoresTolerance,
    breaks="Comparison accepts values inside the declared tolerances.",
)
evidence.known_wrong(
    "widens-integer-domain",
    WidensIntegerDomain,
    breaks="Generated integers remain within the written bounds.",
)
evidence.known_wrong(
    "permits-empty-lists",
    PermitsEmptyLists,
    breaks="A nonempty list domain never generates an empty list.",
)
evidence.known_wrong(
    "shifts-dependency",
    ShiftsDependency,
    breaks="Dependent domains use the actual value of their dependency.",
)
evidence.known_wrong(
    "drops-composed-step",
    DropsComposedStep,
    breaks="Composition executes every step in the written order.",
)
evidence.known_wrong(
    "reuses-state", ReusesState, breaks="Every path execution starts with fresh state."
)
evidence.known_wrong(
    "skips-mapping-keys",
    SkipsMappingKeys,
    breaks="Exclusion searches mapping keys as well as values.",
)
evidence.known_wrong(
    "ignores-dataclass-fields",
    IgnoresDataclassFields,
    breaks="Exclusion descends into declared dataclass fields.",
)
