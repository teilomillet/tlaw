"""Small pytest reporting hooks used by ``tlaw check``."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import hypothesis
import pytest

from .diagnostics import DIAGNOSTICS_ENV, DiagnosticPhase, RunDiagnostic
from .obligations import OBLIGATIONS_ENV, CaseProgress, LawStatus, Obligation
from .replay import MODE_ENV, REPLAY_ENV, SEED_ENV, replay_names

EXPLAIN_ENV = "TLAW_EXPLAIN"


@dataclass
class _LawRun:
    identity: str
    progress: CaseProgress
    phases: dict[str, LawStatus] = field(default_factory=dict)

    def obligation(self, nodeid: str) -> Obligation:
        status = self.phases.get("call", "not_run")
        if set(self.phases) != {"setup", "call", "teardown"}:
            status = "not_run"
        incomplete: tuple[LawStatus, ...] = ("error", "skipped", "xfailed", "xpassed")
        for outcome in incomplete:
            if outcome in self.phases.values():
                status = outcome
                break
        evidence = asdict(self.progress)
        evidence["exhausted"] = evidence["exhausted"] and status == "passed"
        return Obligation(self.identity, nodeid, status, **evidence)


_LAW_RUNS = pytest.StashKey[dict[str, _LawRun]]()
_DIAGNOSTICS = pytest.StashKey[dict[tuple[str, DiagnosticPhase], RunDiagnostic]]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_DIAGNOSTICS] = {}


def _record_diagnostic(
    config: pytest.Config,
    report: pytest.TestReport | pytest.CollectReport,
    *,
    phase: DiagnosticPhase,
    error: BaseException | None = None,
    identity: str | None = None,
) -> None:
    try:
        detail = report.longreprtext
    except Exception:
        detail = "<pytest exception detail could not be rendered>"
    reason = getattr(report, "wasxfail", None)
    if reason:
        detail += f"\nxfail marker: {reason}"
    if error is None:
        diagnostic = RunDiagnostic(
            report.nodeid, phase, None, detail or report.outcome, detail, identity
        )
    else:
        diagnostic = RunDiagnostic.from_exception(
            error, nodeid=report.nodeid, phase=phase, detail=detail, identity=identity
        )
    config.stash[_DIAGNOSTICS][report.nodeid, phase] = diagnostic


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    if report.failed or report.skipped:
        _record_diagnostic(collector.config, report, phase="collect")
    return report


def pytest_exception_interact(
    node: pytest.Node,
    call: pytest.CallInfo[None],
    report: pytest.TestReport | pytest.CollectReport,
) -> None:
    # Pytest exposes the actual collection exception through this public hook.
    # Replace the earlier formatted-only entry; never inspect report internals.
    if call.when == "collect" and call.excinfo is not None:
        _record_diagnostic(
            node.config, report, phase="collect", error=call.excinfo.value
        )


@pytest.hookimpl(tryfirst=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if os.environ.get(REPLAY_ENV) and getattr(
        metafunc.function, "__tlaw_replay_identity__", None
    ):
        # Retained inputs supply these parameters; do not generate new cases.
        metafunc.definition.own_markers[:] = [
            mark
            for mark in metafunc.definition.own_markers
            if mark.name != "parametrize"
        ]


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Replay only retained tlaw failures, never neighboring tests."""
    del session, config
    if os.environ.get(REPLAY_ENV):
        items[:] = [
            item
            for item in items
            if getattr(getattr(item, "obj", None), "__tlaw_replay_identity__", None)
        ]


def pytest_collection_finish(session: pytest.Session) -> None:
    """Explain generation and verify that replay found every retained law."""
    runs = {}
    for item in session.items:
        function = getattr(item, "obj", None)
        identity = getattr(function, "__tlaw_replay_identity__", None) or getattr(
            function, "__tlaw_identity__", None
        )
        if identity is not None:
            progress = getattr(function, "__tlaw_progress__", CaseProgress("sampled"))
            runs[item.nodeid] = _LawRun(identity, replace(progress))
    session.config.stash[_LAW_RUNS] = runs
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if os.environ.get(REPLAY_ENV):
        expected = set(replay_names())
        collected = {
            getattr(getattr(item, "obj", None), "__tlaw_replay_identity__", None)
            for item in session.items
        }
        collected.discard(None)
        missing = tuple(sorted(expected - collected))
        session.config._tlaw_replay_missing = missing
        if reporter is not None:
            reporter.write_line(
                f"tlaw replay: {len(collected)} of {len(expected)} "
                "retained laws collected"
            )

    if os.environ.get(EXPLAIN_ENV) != "1" or reporter is None:
        return

    if os.environ.get(MODE_ENV) == "exhaustive":
        reporter.write_line("tlaw exhaustive domains (no sampling):")
        for item in session.items:
            run = runs.get(item.nodeid)
            if run is not None:
                inputs = getattr(getattr(item, "obj", None), "__tlaw_inputs__", ())
                reporter.write_line(
                    f"  {item.name}: {', '.join(inputs)}; "
                    f"{run.progress.cases_total} declared cases"
                )
        return

    laws: list[tuple[str, tuple[str, ...], int | None, bool]] = []
    for item in session.items:
        function = getattr(item, "obj", None)
        inputs = getattr(function, "__tlaw_inputs__", None)
        if not inputs:
            continue
        laws.append(
            (
                getattr(function, "__tlaw_description__", item.name),
                inputs,
                getattr(function, "__tlaw_max_examples__", None),
                bool(getattr(function, "__tlaw_deterministic__", False)),
            )
        )

    if not laws:
        return

    reporter.write_line(f"tlaw generation (Hypothesis {hypothesis.__version__}):")
    reporter.write_line("  pytest plugins: third-party autoload disabled")
    reporter.write_line("  pytest addopts: project addopts not applied")
    for description, inputs, max_examples, deterministic in laws:
        if os.environ.get(MODE_ENV) == "search" and isinstance(max_examples, int):
            posture = (
                f"up to {max_examples} examples; randomized search, "
                f"seed {os.environ.get(SEED_ENV)}; no example database"
            )
        elif os.environ.get(MODE_ENV) == "search":
            posture = (
                f"explicit strategies; search seed {os.environ.get(SEED_ENV)}; "
                "Hypothesis settings decide examples and phases; "
                "no input-mutation guard"
            )
        elif deterministic and isinstance(max_examples, int):
            posture = (
                f"up to {max_examples} examples; deterministic, no example database"
            )
        else:
            # The explicit form defers to ordinary Hypothesis settings.
            # That is a real difference in evidence posture — randomized
            # generation and no input-mutation guard — and it must be
            # stated where the user is already looking, not discovered.
            posture = (
                "explicit strategies; Hypothesis settings decide examples "
                "and randomization; no input-mutation guard"
            )
        reporter.write_line(f"  {description}: {', '.join(inputs)}; {posture}")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Parametrized items share a function, but never share execution counts."""
    progress = getattr(getattr(item, "obj", None), "__tlaw_progress__", None)
    if progress is not None:
        progress.cases_checked = None if progress.method == "sampled" else 0
        progress.exhausted = False


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Observe finalized pytest outcomes, including skips and expected failures."""
    report = yield
    run = item.config.stash.get(_LAW_RUNS, {}).get(item.nodeid)
    if report.failed or report.skipped or hasattr(report, "wasxfail"):
        _record_diagnostic(
            item.config,
            report,
            phase=report.when,
            error=None if call.excinfo is None else call.excinfo.value,
            identity=None if run is None else run.identity,
        )
    if run is None:
        return report
    status: LawStatus = report.outcome
    if hasattr(report, "wasxfail"):
        status = "xfailed" if report.skipped else "xpassed"
    elif (
        report.when == "call"
        and report.failed
        and call.excinfo is None
        and item.get_closest_marker("xfail") is not None
    ):
        # Strict XPASS is a pytest failure without an exception from the law.
        status = "xpassed"
    elif report.failed and report.when != "call":
        status = "error"
    run.phases[report.when] = status
    if report.when == "call":
        progress = getattr(getattr(item, "obj", None), "__tlaw_progress__", None)
        if progress is not None:
            run.progress = replace(progress)
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write execution evidence and reject missing retained laws."""
    del exitstatus
    destination = os.environ.get(DIAGNOSTICS_ENV)
    if destination is not None:
        diagnostics = session.config.stash.get(_DIAGNOSTICS, {})
        try:
            Path(destination).write_text(
                json.dumps([entry.to_dict() for entry in diagnostics.values()]) + "\n",
                encoding="utf-8",
            )
        except OSError:
            # An unavailable explanation cannot suppress the obligation receipt
            # or change pytest's outcome. The parent reports the unreadable file.
            pass
    destination = os.environ.get(OBLIGATIONS_ENV)
    if destination is not None:
        runs = session.config.stash.get(_LAW_RUNS, {})
        obligations = [runs[nodeid].obligation(nodeid) for nodeid in sorted(runs)]
        Path(destination).write_text(
            json.dumps([law.to_dict() for law in obligations]) + "\n",
            encoding="utf-8",
        )
    missing = getattr(session.config, "_tlaw_replay_missing", ())
    if not missing:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            "tlaw replay could not find retained laws: " + ", ".join(missing)
        )
    session.exitstatus = pytest.ExitCode.USAGE_ERROR
