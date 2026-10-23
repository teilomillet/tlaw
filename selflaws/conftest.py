"""Keep explicit-strategy self-laws deterministic and independent of local state."""

from hypothesis import settings


def pytest_configure() -> None:
    settings.register_profile(
        "tlaw-selflaws",
        max_examples=60,
        derandomize=True,
        database=None,
        deadline=None,
    )
    settings.load_profile("tlaw-selflaws")
