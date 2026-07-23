"""Retained counterexamples for ``tlaw check`` search and replay."""

from __future__ import annotations

import base64
import inspect
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

from hypothesis.control import current_build_context, currently_in_test_context

from .audit import ManifestError
from .comparison import Comparison


CAPTURE_ENV = "TLAW_CAPTURE"
MODE_ENV = "TLAW_MODE"
REPLAY_ENV = "TLAW_REPLAY"
REPLAY_ROOT_ENV = "TLAW_REPLAY_ROOT"
SEED_ENV = "TLAW_SEED"
REPORT_SCHEMA = "tlaw.check/v1"
_CAPTURE_SCHEMA = "tlaw.capture/v1"


class EvidenceEncodingError(ValueError):
    """A generated value cannot be retained without guessing."""


def _encode(value: Any, *, active: set[int]) -> dict[str, Any]:
    if value is None:
        return {"type": "none"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value):
            rendered = "nan"
        elif math.isinf(value):
            rendered = "inf" if value > 0 else "-inf"
        else:
            rendered = repr(value)
        return {"type": "float", "value": rendered}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "base64": base64.b64encode(value).decode("ascii"),
        }

    if isinstance(value, list | tuple | dict | set | frozenset):
        identity = id(value)
        if identity in active:
            raise EvidenceEncodingError("cyclic containers are not replayable")
        active.add(identity)
        try:
            if isinstance(value, list | tuple):
                return {
                    "type": "list" if isinstance(value, list) else "tuple",
                    "items": [_encode(item, active=active) for item in value],
                }
            if isinstance(value, dict):
                entries = [
                    {
                        "key": _encode(key, active=active),
                        "value": _encode(item, active=active),
                    }
                    for key, item in value.items()
                ]
                return {"type": "dict", "entries": entries}
            items = [_encode(item, active=active) for item in value]
            items.sort(key=lambda item: json.dumps(item, sort_keys=True))
            return {
                "type": "set" if isinstance(value, set) else "frozenset",
                "items": items,
            }
        finally:
            active.remove(identity)

    raise EvidenceEncodingError(
        f"type {type(value).__module__}.{type(value).__qualname__} "
        "has no safe replay encoding"
    )


def encode_value(value: Any) -> dict[str, Any]:
    """Encode supported immutable and container values as typed JSON."""
    return _encode(value, active=set())


def decode_value(encoded: Any) -> Any:
    """Decode one value while rejecting malformed or unknown encodings."""
    if not isinstance(encoded, dict) or not isinstance(encoded.get("type"), str):
        raise ManifestError("replay input is not a typed tlaw value")
    kind = encoded["type"]
    try:
        if kind == "none":
            return None
        if kind == "bool":
            value = encoded["value"]
            if not isinstance(value, bool):
                raise ValueError
            return value
        if kind == "int":
            value = encoded["value"]
            if not isinstance(value, str):
                raise ValueError
            return int(value)
        if kind == "float":
            value = encoded["value"]
            if not isinstance(value, str):
                raise ValueError
            return float(value)
        if kind == "str":
            value = encoded["value"]
            if not isinstance(value, str):
                raise ValueError
            return value
        if kind == "bytes":
            value = encoded["base64"]
            if not isinstance(value, str):
                raise ValueError
            return base64.b64decode(value, validate=True)
        if kind in {"list", "tuple", "set", "frozenset"}:
            items = encoded["items"]
            if not isinstance(items, list):
                raise ValueError
            decoded = [decode_value(item) for item in items]
            if kind == "list":
                return decoded
            if kind == "tuple":
                return tuple(decoded)
            if kind == "set":
                return set(decoded)
            return frozenset(decoded)
        if kind == "dict":
            entries = encoded["entries"]
            if not isinstance(entries, list):
                raise ValueError
            result = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError
                result[decode_value(entry["key"])] = decode_value(entry["value"])
            return result
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestError(f"malformed replay value of type {kind!r}") from error
    raise ManifestError(f"unsupported replay value type {kind!r}")


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception as error:
        return f"<repr raised {type(error).__name__}: {error}>"


def exception_evidence(error: Exception) -> dict[str, str]:
    return {
        "type": f"{type(error).__module__}.{type(error).__qualname__}",
        "message": str(error),
    }


def _encoded_arguments(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    encoded: dict[str, Any] | None = {}
    capture_error = None
    for name, value in arguments.items():
        try:
            assert encoded is not None
            encoded[name] = encode_value(value)
        except EvidenceEncodingError as encoding_error:
            encoded = None
            capture_error = (
                f"cannot retain input {name!r} of type {type(value).__name__}: "
                f"{encoding_error}"
            )
            break
    return encoded, capture_error


def prepare_failure_inputs(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None] | None:
    """Freeze exact inputs before a final Hypothesis rerun calls the law."""
    if not os.environ.get(CAPTURE_ENV):
        return None
    if currently_in_test_context() and not current_build_context().is_final:
        return None
    return _encoded_arguments(arguments)


def _comparison_evidence(comparison: Comparison | None) -> dict[str, Any] | None:
    if comparison is None:
        return None
    difference = comparison.difference
    return {
        "left": _safe_repr(comparison.left),
        "right": _safe_repr(comparison.right),
        "within": comparison.within,
        "relative": comparison.relative,
        "first_difference": (
            None
            if difference is None
            else {
                "path": difference.path,
                "left": _safe_repr(difference.left),
                "right": _safe_repr(difference.right),
                "reason": difference.reason,
            }
        ),
    }


def _read_capture(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": _CAPTURE_SCHEMA, "failures": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": _CAPTURE_SCHEMA, "failures": []}
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != _CAPTURE_SCHEMA
        or not isinstance(payload.get("failures"), list)
    ):
        return {"schema": _CAPTURE_SCHEMA, "failures": []}
    return payload


def capture_failure(
    function: Callable[..., Any],
    *,
    description: str,
    generation: str,
    arguments: dict[str, Any],
    prepared_inputs: tuple[dict[str, Any] | None, str | None] | None,
    error: Exception,
    comparison: Comparison | None,
) -> None:
    """Retain Hypothesis's final rerun of each minimal failing example."""
    destination = os.environ.get(CAPTURE_ENV)
    if not destination:
        return
    # Hypothesis explicitly reruns each minimal failing example with
    # BuildContext.is_final. Capturing only those reruns avoids treating an
    # intermediate shrink candidate as retained evidence, and keeps distinct
    # failure origins from the same law.
    if currently_in_test_context() and not current_build_context().is_final:
        return
    source = inspect.getsourcefile(function) or inspect.getfile(function)
    identity = f"{Path(source).resolve()}::{function.__name__}"
    encoded, capture_error = (
        _encoded_arguments(arguments) if prepared_inputs is None else prepared_inputs
    )

    failure = {
        "identity": identity,
        "name": function.__name__,
        "description": description,
        "generation": generation,
        "source_path": str(Path(source).resolve()),
        "source_line": function.__code__.co_firstlineno,
        "inputs": encoded,
        "capture_error": capture_error,
        "exception": exception_evidence(error),
        "comparison": _comparison_evidence(comparison),
    }
    payload = _read_capture(Path(destination))
    if failure not in payload["failures"]:
        payload["failures"].append(failure)
    path = Path(destination)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_capture(path: Path) -> tuple[dict[str, Any], ...]:
    payload = _read_capture(path)
    return tuple(
        sorted(
            payload["failures"],
            key=lambda failure: (
                failure["identity"],
                json.dumps(failure.get("inputs"), sort_keys=True),
                failure["exception"]["type"],
                failure["exception"]["message"],
            ),
        )
    )


def load_report(path: str | Path) -> dict[str, Any]:
    """Load and validate a retained public report without executing its law."""
    report_path = Path(path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ManifestError(
            f"could not read replay report {report_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ManifestError(
            f"replay report is not valid JSON: {report_path}: {error}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema") != REPORT_SCHEMA:
        raise ManifestError(f"replay report must use schema {REPORT_SCHEMA!r}")
    law_file = payload.get("law_file")
    failures = payload.get("failures")
    if not isinstance(law_file, str) or not law_file:
        raise ManifestError("replay report has no law_file")
    if not isinstance(failures, list) or not failures:
        raise ManifestError("replay report has no retained failures")

    for failure in failures:
        if not isinstance(failure, dict):
            raise ManifestError("replay report contains a malformed failure")
        name = failure.get("name")
        inputs = failure.get("inputs")
        exception = failure.get("exception")
        identity = failure.get("identity")
        source = failure.get("source")
        generation = failure.get("generation")
        if not isinstance(name, str) or not name:
            raise ManifestError("replay report has a missing law name")
        if (
            not isinstance(identity, str)
            or not isinstance(source, dict)
            or not isinstance(source.get("path"), str)
            or not isinstance(source.get("line"), int)
        ):
            raise ManifestError(f"replay failure {name!r} has no source identity")
        if identity != f"{source['path']}::{name}":
            raise ManifestError(
                f"replay failure {name!r} has an inconsistent source identity"
            )
        if not isinstance(generation, str) or not generation:
            raise ManifestError(f"replay failure {name!r} has no generation posture")
        if not isinstance(inputs, dict):
            raise ManifestError(f"replay failure {name!r} has no retained inputs")
        for encoded in inputs.values():
            decode_value(encoded)
        if (
            not isinstance(exception, dict)
            or not isinstance(exception.get("type"), str)
            or not isinstance(exception.get("message"), str)
        ):
            raise ManifestError(f"replay failure {name!r} has no exception fingerprint")
    return payload


def report_from_environment() -> dict[str, Any] | None:
    path = os.environ.get(REPLAY_ENV)
    return None if path is None else load_report(path)


def replay_failures(function: Callable[..., Any]) -> tuple[dict[str, Any], ...]:
    payload = report_from_environment()
    if payload is None:
        return ()
    source = Path(
        inspect.getsourcefile(function) or inspect.getfile(function)
    ).resolve()
    root = Path(os.environ.get(REPLAY_ROOT_ENV, Path.cwd())).resolve()
    return tuple(
        failure
        for failure in payload["failures"]
        if failure["name"] == function.__name__
        and (root / failure["source"]["path"]).resolve() == source
    )


def replay_names() -> tuple[str, ...]:
    payload = report_from_environment()
    if payload is None:
        return ()
    return tuple(sorted({failure["identity"] for failure in payload["failures"]}))


def decoded_arguments(
    function: Callable[..., Any],
    failure: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    encoded = failure["inputs"]
    values = {name: decode_value(value) for name, value in encoded.items()}
    positional = []
    keywords = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.name not in values:
            raise ManifestError(
                f"replay failure {failure['name']!r} has no input {parameter.name!r}"
            )
        value = values.pop(parameter.name)
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            if not isinstance(value, tuple | list):
                raise ManifestError(
                    f"replay variadic input {parameter.name!r} is not a sequence"
                )
            positional.extend(value)
        elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
            if not isinstance(value, dict):
                raise ManifestError(
                    f"replay variadic input {parameter.name!r} is not a mapping"
                )
            keywords.update(value)
        else:
            keywords[parameter.name] = value
    if values:
        names = ", ".join(sorted(values))
        raise ManifestError(f"replay report contains unknown inputs: {names}")
    return tuple(positional), keywords


__all__ = [
    "CAPTURE_ENV",
    "MODE_ENV",
    "REPLAY_ENV",
    "REPLAY_ROOT_ENV",
    "REPORT_SCHEMA",
    "SEED_ENV",
    "capture_failure",
    "decode_value",
    "decoded_arguments",
    "encode_value",
    "exception_evidence",
    "load_report",
    "prepare_failure_inputs",
    "read_capture",
    "replay_failures",
    "replay_names",
    "report_from_environment",
]
