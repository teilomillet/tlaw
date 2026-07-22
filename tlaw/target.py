from __future__ import annotations

import importlib
import os
from typing import Any

from .audit import TARGET_ENV


def load_target(reference: str | None = None) -> Any:
    """Load the implementation selected by a tlaw audit subprocess."""

    selected = reference or os.environ.get(TARGET_ENV)
    if not selected:
        raise RuntimeError(
            f"No implementation selected. Set {TARGET_ENV} or pass 'module:attribute'."
        )
    module_name, separator, attribute = selected.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("implementation target must use 'module:attribute'")
    module = importlib.import_module(module_name)
    try:
        target: Any = module
        for part in attribute.split("."):
            if not part:
                raise AttributeError(attribute)
            target = getattr(target, part)
        return target
    except AttributeError as error:
        raise AttributeError(
            f"implementation target {selected!r} does not exist"
        ) from error
