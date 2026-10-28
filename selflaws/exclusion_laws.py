"""Grade exclusion with plain Python containment, never with another tlaw exclusion."""

from dataclasses import dataclass
import os

import tlaw
from tlaw.audit import TARGET_ENV
from tlaw.target import load_target


subject = load_target(os.environ.get(TARGET_ENV, "implementations:Core"))
Small = tlaw.integer(0, 100)


@dataclass(frozen=True)
class Envelope:
    note: str


@tlaw.law
def mapping_keys_are_searched_for_leaks(value: Small):
    secret = f"secret-{value}"
    clean = subject.excludes({f"public-{value}": "ok"}, secret)
    leaked = subject.excludes({f"prefix-{secret}": "ok"}, secret)
    return clean.matched and not leaked.matched and leaked.leak is not None


@tlaw.law
def dataclass_fields_are_searched_and_located(value: Small):
    secret = f"secret-{value}"
    clean = subject.excludes(Envelope(note=f"public-{value}"), secret)
    leaked = subject.excludes(Envelope(note=f"prefix-{secret}"), secret)
    return (
        clean.matched
        and not leaked.matched
        and leaked.leak is not None
        and leaked.leak.path == ".note"
    )
