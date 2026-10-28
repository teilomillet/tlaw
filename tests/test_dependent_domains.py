"""Laws for dependent domains: relations satisfied by construction.

Designed from the hardest available case (retrain's three-way linked
shapes), not the easiest, so the vocabulary is not shaped by a toy.
"""

from __future__ import annotations

from typing import Annotated

from hypothesis import settings
import pytest

import tlaw


Budget = tlaw.integer(1, 30)
Lengths = tlaw.depends(
    lambda budget: tlaw.list_of(tlaw.integer(1, budget), max_size=12)
)

# retrain's three-way linkage: rewards, logprobs, and masks must all
# describe the same episodes and tokens.
Episodes = tlaw.integer(1, 4)
Tokens = tlaw.integer(1, 3)
Rewards = tlaw.depends(
    lambda episodes: tlaw.list_of(
        tlaw.number(0, 1), min_size=episodes, max_size=episodes
    )
)
Logprobs = tlaw.depends(
    lambda episodes, tokens: tlaw.list_of(
        tlaw.list_of(tlaw.number(-10, 0), min_size=tokens, max_size=tokens),
        min_size=episodes,
        max_size=episodes,
    )
)


def test_a_dependent_domain_satisfies_its_relation_by_construction() -> None:
    @tlaw.law
    def every_length_fits_the_budget(budget: Budget, lengths: Lengths):
        return all(1 <= length <= budget for length in lengths)

    every_length_fits_the_budget()


def test_the_three_way_linked_shapes_agree() -> None:
    @tlaw.law
    def linked_shapes_agree(
        episodes: Episodes,
        tokens: Tokens,
        rewards: Rewards,
        logprobs: Logprobs,
    ):
        return (
            len(rewards) == episodes
            and len(logprobs) == episodes
            and all(len(row) == tokens for row in logprobs)
        )

    linked_shapes_agree()


def test_dependent_laws_still_fail_honestly() -> None:
    # Generation must not accidentally make every law true: a false
    # claim over the dependent domain must still be refuted.
    @tlaw.law
    def no_length_equals_one(budget: Budget, lengths: Lengths):
        return all(length != 1 for length in lengths)

    with pytest.raises(AssertionError):
        no_length_equals_one()


def test_the_dependency_is_visible_in_explain_metadata() -> None:
    @tlaw.law
    def anything(budget: Budget, lengths: Lengths):
        return True

    # The declared contract states the dependency instead of a wider
    # domain the law never exercises.
    assert anything.__tlaw_inputs__ == (
        "budget: integer(1, 30)",
        "lengths: depends(budget)",
    )


def test_the_mutation_guard_survives_the_composite_draw() -> None:
    @tlaw.law
    def quietly_destructive(budget: Budget, lengths: Lengths):
        lengths.clear()
        return True

    with pytest.raises(AssertionError) as caught:
        quietly_destructive()

    assert "mutating generated input" in str(caught.value)


# These live at module level because domains used as annotations must:
# postponed string annotations resolve against module globals.
UnknownDependency = tlaw.depends(lambda nonexistent: tlaw.integer(1, nonexistent))
CyclicFirst = tlaw.depends(lambda second: tlaw.integer(1, max(second, 1)))
CyclicSecond = tlaw.depends(lambda first: tlaw.integer(1, max(first, 1)))
WrongBuilder = tlaw.depends(lambda budget: "not a domain")
AnnotatedBudget = Annotated[int, Budget]
AnnotatedLengths = Annotated[list[int], Lengths]
ContradictingAnnotatedLengths = Annotated[str, Lengths]
ListWrappedDependent = list[Lengths]


def test_an_unknown_dependency_is_refused_at_decoration() -> None:
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def uses_unknown(budget: Budget, value: UnknownDependency):
            return True

    assert "nonexistent" in str(refusal.value)


def test_a_cycle_is_refused_at_decoration() -> None:
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def circular(first: CyclicFirst, second: CyclicSecond):
            return True

    assert "cycle" in str(refusal.value)


def test_depends_rejects_builders_it_cannot_match() -> None:
    def positional_only(value: int, /) -> tlaw.Domain:
        return tlaw.integer(1, value)

    with pytest.raises(ValueError):
        tlaw.depends(lambda: tlaw.integer(1, 2))
    with pytest.raises(TypeError):
        tlaw.depends(positional_only)
    with pytest.raises(TypeError):
        tlaw.depends(lambda *args: tlaw.integer(1, 2))
    with pytest.raises(TypeError):
        tlaw.depends("not a function")


def test_a_builder_returning_a_non_domain_is_refused() -> None:
    @tlaw.law
    def uses_wrong(budget: Budget, value: WrongBuilder):
        return True

    with pytest.raises(TypeError) as refusal:
        uses_wrong()

    assert "must build a tlaw domain" in str(refusal.value)


def test_annotated_dependent_domains_enforce_relations_and_base_types() -> None:
    @tlaw.law
    def annotated_lengths_fit_budget(
        budget: AnnotatedBudget,
        lengths: AnnotatedLengths,
    ) -> bool:
        return all(1 <= length <= budget for length in lengths)

    annotated_lengths_fit_budget()
    assert annotated_lengths_fit_budget.__tlaw_inputs__ == (
        "budget: integer(1, 30)",
        "lengths: depends(budget)",
    )

    @tlaw.law
    def contradicting_base(
        budget: AnnotatedBudget,
        lengths: ContradictingAnnotatedLengths,
    ) -> bool:
        return isinstance(lengths, list)

    with pytest.raises(TypeError, match="cannot hold what"):
        contradicting_base()

    with pytest.raises(TypeError, match="position tlaw cannot enforce yet"):

        @tlaw.law
        def nested_list_of_dependent(
            budget: Budget,
            batches: ListWrappedDependent,
        ) -> bool:
            return bool(batches)


def test_explicit_law_supports_dependent_domains() -> None:
    @tlaw.law(
        "explicit dependent lengths fit budget",
        budget=Budget,
        lengths=Lengths,
    )
    def explicit_lengths_fit_budget(budget: int, lengths: list[int]) -> bool:
        return all(1 <= length <= budget for length in lengths)

    explicit_lengths_fit_budget()
    assert explicit_lengths_fit_budget.__tlaw_inputs__ == (
        "budget: integer(1, 30)",
        "lengths: depends(budget)",
    )

    with pytest.raises(TypeError, match="unknown parameters: extra"):

        @tlaw.law(
            "unknown explicit parameter",
            budget=Budget,
            lengths=Lengths,
            extra=Budget,
        )
        def bad_explicit(budget: int, lengths: list[int]) -> bool:
            return True


@pytest.mark.parametrize(
    "parameters", ["resource, budget, lengths", "budget, lengths, *, resource"]
)
def test_explicit_dependent_laws_accept_pytest_fixtures(tmp_path, parameters):
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        "import pytest\nimport tlaw\n"
        "@pytest.fixture(scope='module')\n"
        "def resource():\n    return 'fixture value'\n"
        "@tlaw.law('fixture inputs', budget=tlaw.integer(1, 3),\n"
        "    lengths=tlaw.depends(lambda budget: tlaw.list_of(\n"
        "        tlaw.integer(1, budget), max_size=3)))\n"
        f"def uses_fixture({parameters}):\n"
        "    return resource == 'fixture value' and all(\n"
        "        1 <= length <= budget for length in lengths)\n",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 0, result.output
    assert "1 passed" in result.output


@pytest.mark.parametrize("mark_inside", [False, True])
def test_explicit_dependent_laws_preserve_pytest_parametrization(tmp_path, mark_inside):
    law_file = tmp_path / "laws.py"
    mark = "@pytest.mark.parametrize('resource', ['first', 'second'])\n"
    law = (
        "@tlaw.law('parametrized inputs', budget=tlaw.integer(1, 3),\n"
        "    value=tlaw.depends(lambda budget: tlaw.integer(1, budget)))\n"
    )
    law_file.write_text(
        "import pytest\nimport tlaw\n"
        + (law + mark if mark_inside else mark + law)
        + "def parametrized(resource, budget, value):\n"
        "    return resource in {'first', 'second'} and 1 <= value <= budget\n",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    assert result.exit_code == 0, result.output
    assert "2 passed" in result.output


def test_explicit_dependent_laws_preserve_inner_skip_mark(tmp_path):
    law_file = tmp_path / "laws.py"
    law_file.write_text(
        "import pytest\nimport tlaw\n"
        "@tlaw.law('skipped law', budget=tlaw.integer(1, 3),\n"
        "    value=tlaw.depends(lambda budget: tlaw.integer(1, budget)))\n"
        "@pytest.mark.skip(reason='deliberately unavailable')\n"
        "def skipped(budget, value):\n"
        "    raise AssertionError('skipped law executed')\n",
        encoding="utf-8",
    )

    result = tlaw.check(law_file)

    # A skipped law remains incomplete evidence, never a passing law.
    assert result.exit_code == 2, result.output
    assert "1 skipped" in result.output
    assert "skipped law executed" not in result.output


def test_explicit_dependent_laws_preserve_inner_hypothesis_settings():
    observations = []

    @tlaw.law("bounded sampling", budget=Budget, lengths=Lengths)
    @settings(max_examples=7, derandomize=True, database=None)
    def bounded_sampling(budget, lengths):
        observations.append((budget, lengths))
        return all(1 <= length <= budget for length in lengths)

    bounded_sampling()

    assert len(observations) == 7
