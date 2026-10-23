"""Execution evidence shared by the pytest child and its check/replay parent."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from .audit import ManifestError


OBLIGATIONS_ENV = "TLAW_OBLIGATIONS"
LawStatus = Literal[
    "passed", "failed", "skipped", "xfailed", "xpassed", "error", "not_run"
]
EvidenceMethod = Literal["sampled", "exhaustive", "replay"]


@dataclass
class CaseProgress:
    """Mutable only while one pytest case executes; the receipt freezes it."""

    method: EvidenceMethod
    cases_checked: int | None = None
    cases_total: int | None = None
    exhausted: bool = False
    list_bounds: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class Obligation:
    identity: str
    nodeid: str
    status: LawStatus
    method: EvidenceMethod = "sampled"
    cases_checked: int | None = None
    cases_total: int | None = None
    exhausted: bool = False
    list_bounds: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        if self.status == "failed":
            return True
        if self.status != "passed":
            return False
        if self.method == "sampled":
            return True
        return self.cases_checked == self.cases_total and (
            self.method == "replay" or self.exhausted
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_line(law: Obligation) -> str:
    if law.method == "sampled":
        return f"sampled: {law.nodeid}; total input space not enumerated"
    bounds = ""
    if law.list_bounds:
        lengths = ", ".join(
            f"{name}={bound['min_size']}..{bound['max_size']}"
            for name, bound in law.list_bounds.items()
        )
        bounds = f"; lengths: {lengths}"
    outcome = "all declared cases passed" if law.exhausted else "not exhausted"
    return (
        f"{law.method}: {law.nodeid}; "
        f"{law.cases_checked}/{law.cases_total} cases checked{bounds}; {outcome}"
    )


def obligations_complete(obligations: tuple[Obligation, ...]) -> bool:
    """Success requires evidence for at least one fully executed law case."""
    return bool(obligations) and all(law.completed for law in obligations)


def read_obligations(path: Path) -> tuple[Obligation, ...]:
    """Missing or malformed execution evidence cannot justify success."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManifestError(
            f"could not read law execution evidence: {error}"
        ) from error
    if not isinstance(payload, list):
        raise ManifestError("law execution evidence must be a list")
    obligations = []
    nodeids: set[str] = set()
    for entry in payload:
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {
                "identity",
                "nodeid",
                "status",
                "method",
                "cases_checked",
                "cases_total",
                "exhausted",
                "list_bounds",
            }
            or not isinstance(entry["identity"], str)
            or not entry["identity"]
            or "::" not in entry["identity"]
            or not isinstance(entry["nodeid"], str)
            or not entry["nodeid"]
            or entry["status"] not in get_args(LawStatus)
            or entry["nodeid"] in nodeids
            or entry["method"] not in get_args(EvidenceMethod)
            or type(entry["exhausted"]) is not bool
        ):
            raise ManifestError("law execution evidence contains an invalid obligation")
        checked, total = entry["cases_checked"], entry["cases_total"]
        if entry["method"] == "sampled":
            valid_counts = checked is None and total is None and not entry["exhausted"]
        else:
            valid_counts = (
                type(checked) is int
                and type(total) is int
                and 0 <= checked <= total
                and total > 0
            )
        if not valid_counts or (
            entry["exhausted"]
            and (
                entry["method"] != "exhaustive"
                or entry["status"] != "passed"
                or checked != total
            )
        ):
            raise ManifestError("law execution evidence contains invalid case counts")
        bounds = entry["list_bounds"]
        if (
            not isinstance(bounds, dict)
            or (entry["method"] != "exhaustive" and bounds)
            or any(
                not isinstance(name, str)
                or not name
                or not isinstance(bound, dict)
                or set(bound) != {"min_size", "max_size"}
                or type(bound["min_size"]) is not int
                or type(bound["max_size"]) is not int
                or not 0 <= bound["min_size"] <= bound["max_size"]
                for name, bound in bounds.items()
            )
        ):
            raise ManifestError("law execution evidence contains invalid list bounds")
        obligations.append(Obligation(**entry))
        nodeids.add(entry["nodeid"])
    return tuple(obligations)
