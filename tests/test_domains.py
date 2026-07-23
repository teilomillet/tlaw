"""Laws for tlaw domains: visible input contracts that generate inputs.

Domains here are module-level names deliberately: annotations written as
strings (this file uses ``from __future__ import annotations``) resolve
against module globals, which is exactly how a real laws file shares one
domain across its laws.
"""

from __future__ import annotations

import math
from typing import Annotated, Protocol

import pytest
from hypothesis import given
from hypothesis import strategies as st

import tlaw


Reward = tlaw.number(0, 1)
Rewards = tlaw.list_of(Reward, min_size=1, max_size=8)
Shift = tlaw.number(-1, 1)

# The type-checker-clean spellings from the probe's syntax candidate:
# the base type is for the reader and the checker, the domain is what
# tlaw enforces.
BoundedReward = Annotated[float, tlaw.number(0, 1)]
type AliasedReward = Annotated[float, tlaw.number(0, 1)]


@given(data=st.data())
def test_number_generates_only_finite_floats_inside_its_bounds(data) -> None:
    value = data.draw(Reward.strategy)

    assert isinstance(value, float)
    assert math.isfinite(value)
    assert 0 <= value <= 1


@given(data=st.data())
def test_list_of_respects_element_domain_and_size_bounds(data) -> None:
    values = data.draw(Rewards.strategy)

    assert 1 <= len(values) <= 8
    assert all(0 <= value <= 1 for value in values)


def test_the_domain_description_is_its_repr() -> None:
    # The description is what a reviewer and --explain see; generation
    # must never be an invisible spec.
    assert repr(Reward) == "number(0, 1)"
    assert repr(Rewards) == "list_of(number(0, 1), min_size=1, max_size=8)"
    assert repr(tlaw.list_of(int)) == "list_of(int)"
    assert repr(tlaw.integer(1, 8)) == "integer(1, 8)"


GpuCount = tlaw.integer(1, 8)


@given(data=st.data())
def test_integer_generates_only_bounded_integers(data) -> None:
    value = data.draw(GpuCount.strategy)

    assert isinstance(value, int) and not isinstance(value, bool)
    assert 1 <= value <= 8


def test_integer_rejects_impossible_or_non_integer_bounds() -> None:
    with pytest.raises(ValueError):
        tlaw.integer(8, 1)
    with pytest.raises(TypeError):
        tlaw.integer(0.5, 2)
    with pytest.raises(TypeError):
        tlaw.integer(True, 2)


def test_integer_domains_work_in_the_bare_form() -> None:
    # The kvfit probe shape: bounded integer inputs in the bare form,
    # keeping the deterministic posture and the mutation guard.
    @tlaw.law
    def every_divisor_pairing_covers_the_gpus(gpus: GpuCount):
        return all(
            gpus % size == 0 or gpus // size * size < gpus
            for size in range(1, gpus + 1)
        )

    every_divisor_pairing_covers_the_gpus()


def test_impossible_domains_are_rejected_at_definition_time() -> None:
    with pytest.raises(ValueError):
        tlaw.number(1, 0)
    with pytest.raises(ValueError):
        tlaw.number(0, math.inf)
    with pytest.raises(TypeError):
        tlaw.number(True, 1)
    with pytest.raises(ValueError):
        tlaw.list_of(Reward, min_size=3, max_size=2)
    with pytest.raises(TypeError):
        tlaw.list_of("not a domain")


def test_a_bare_law_generates_from_domain_annotations() -> None:
    @tlaw.law
    def rewards_stay_inside_their_domain(rewards: Rewards, amount: Shift):
        return all(0 <= reward <= 1 for reward in rewards) and -1 <= amount <= 1

    rewards_stay_inside_their_domain()


def test_a_bare_law_may_mix_domains_with_plain_types() -> None:
    @tlaw.law
    def scaling_keeps_length(rewards: Rewards, repeat: int):
        return len(rewards * (abs(repeat) % 3)) == len(rewards) * (abs(repeat) % 3)

    scaling_keeps_length()


def test_a_domain_law_still_fails_honestly() -> None:
    @tlaw.law
    def rewards_never_exceed_one_half(rewards: Rewards):
        return all(reward <= 0.5 for reward in rewards)

    with pytest.raises(AssertionError):
        rewards_never_exceed_one_half()


def test_a_domain_law_returning_same_reports_both_sides() -> None:
    @tlaw.law
    def shifted_rewards_are_unchanged(rewards: Rewards, amount: Shift):
        shifted = [reward + amount for reward in rewards]
        return tlaw.same(shifted, rewards, within=1e-12)

    with pytest.raises(AssertionError) as failure:
        shifted_rewards_are_unchanged()

    message = str(failure.value)
    assert "first difference at value[0]" in message
    assert "absolute difference" in message


def test_annotated_domains_generate_within_their_bounds() -> None:
    @tlaw.law
    def bounded_rewards_stay_bounded(reward: BoundedReward):
        return 0 <= reward <= 1

    bounded_rewards_stay_bounded()


def test_the_probe_candidate_spelling_works_through_a_type_alias() -> None:
    # rewards: list[Reward] with `type Reward = Annotated[float, ...]` is
    # the checker-clean shape documented in docs/current-behavior.md.
    @tlaw.law
    def aliased_rewards_stay_bounded(rewards: list[AliasedReward]):
        return all(0 <= reward <= 1 for reward in rewards)

    aliased_rewards_stay_bounded()


def test_foreign_annotated_metadata_is_still_rejected() -> None:
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def unenforceable(value: Annotated[float, "positive"]):
            return True

    assert "Annotated" in str(refusal.value)


def test_a_domain_mixed_with_foreign_metadata_is_refused() -> None:
    # Refused rather than partially enforced: tlaw cannot honor the
    # foreign metadata, and honoring only the domain would let the
    # signature claim more than generation delivers.
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def ambiguous(value: Annotated[float, tlaw.number(0, 1), "positive"]):
            return True

    assert "value" in str(refusal.value)
    assert "Annotated" in str(refusal.value) or "domain" in str(refusal.value)


def test_a_domain_in_an_unsupported_position_is_refused_not_erased() -> None:
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def nested_too_deep(table: dict[str, BoundedReward]):
            return True

    assert "cannot enforce yet" in str(refusal.value)


def test_mixing_domains_with_an_unannotated_parameter_is_refused() -> None:
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def half_declared(rewards: Rewards, mystery):
            return True

    assert "mystery" in str(refusal.value)


def test_the_mutation_guard_still_covers_domain_generated_inputs() -> None:
    @tlaw.law
    def quietly_destructive(rewards: Rewards):
        rewards.clear()
        return True

    with pytest.raises(AssertionError) as caught:
        quietly_destructive()

    assert "mutating generated input" in str(caught.value)


def test_the_explicit_form_accepts_domains_directly() -> None:
    @tlaw.law("rewards stay bounded", rewards=Rewards)
    def rewards_stay_bounded(rewards):
        return all(0 <= reward <= 1 for reward in rewards)

    rewards_stay_bounded()


def test_explain_metadata_shows_the_domain_description() -> None:
    @tlaw.law
    def rewards_stay_inside(rewards: Rewards):
        return all(0 <= reward <= 1 for reward in rewards)

    assert rewards_stay_inside.__tlaw_inputs__ == (
        "rewards: list_of(number(0, 1), min_size=1, max_size=8)",
    )


Dtypes = tlaw.one_of(["fp8", "fp16", "bf16"])
# The kvfit shape: choices computed from another drawn value.
Divisors = tlaw.depends(
    lambda gpus: tlaw.one_of([size for size in range(1, gpus + 1) if gpus % size == 0])
)


@given(data=st.data())
def test_one_of_generates_only_declared_values(data) -> None:
    assert data.draw(Dtypes.strategy) in ("fp8", "fp16", "bf16")


def test_one_of_describes_its_choices_and_summarises_many() -> None:
    assert repr(Dtypes) == "one_of('fp8', 'fp16', 'bf16')"
    many = tlaw.one_of(range(20))
    # Honest summary rather than an unreadable wall or a silent truncation.
    assert repr(many) == "one_of(0, 1, 2, 3, ... (20 values))"


def test_an_empty_one_of_is_refused_at_definition_time() -> None:
    with pytest.raises(ValueError):
        tlaw.one_of([])


def test_one_of_composes_with_depends_for_computed_choices() -> None:
    @tlaw.law
    def a_chosen_size_always_divides_the_count(gpus: GpuCount, size: Divisors):
        return gpus % size == 0

    a_chosen_size_always_divides_the_count()


# --- Annotated base-type / domain agreement -----------------------------

BadNumberBase = Annotated[str, tlaw.number(0, 1)]
BadIntegerBase = Annotated[str, tlaw.integer(1, 8)]
IntBaseFloatDomain = Annotated[int, tlaw.number(0, 1)]
ObjectBase = Annotated[object, tlaw.number(0, 1)]
UnionBase = Annotated[int | float, tlaw.integer(1, 8)]
StrChoice = Annotated[str, tlaw.one_of(["fp8", "fp16"])]
BadChoiceBase = Annotated[int, tlaw.one_of(["fp8", "fp16"])]
MixedChoice = Annotated[int, tlaw.one_of([1, "wrong type"])]


class HasValue(Protocol):
    value: int


class ConcreteValue:
    value = 1

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ConcreteValue) and other.value == self.value


ProtocolChoice = Annotated[HasValue, tlaw.one_of([ConcreteValue()])]


def test_a_base_type_that_contradicts_its_domain_is_refused() -> None:
    # str claimed to the checker, floats generated: the contract lies.
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def wrong(value: BadNumberBase):
            return True

    message = str(refusal.value)
    assert "never produces" in message
    assert "str" in message and "float" in message


def test_int_base_with_a_float_domain_is_a_proven_mismatch() -> None:
    # int is not a float instance, so this is a real inconsistency.
    with pytest.raises(TypeError):

        @tlaw.law
        def wrong(value: IntBaseFloatDomain):
            return True

    with pytest.raises(TypeError):

        @tlaw.law
        def also_wrong(value: BadIntegerBase):
            return True

    with pytest.raises(TypeError):

        @tlaw.law
        def bad_choice(value: BadChoiceBase):
            return True


def test_a_compatible_or_permissive_base_is_accepted() -> None:
    # object accepts anything; a union that includes the generated type
    # is fine; a matching finite-choice base is fine. None should raise.
    @tlaw.law
    def with_object(value: ObjectBase):
        return isinstance(value, float)

    @tlaw.law
    def with_union(value: UnionBase):
        return isinstance(value, int)

    @tlaw.law
    def with_choice(value: StrChoice):
        return isinstance(value, str)

    with_object()
    with_union()
    with_choice()


def test_every_type_in_a_mixed_domain_must_fit_the_annotated_base() -> None:
    with pytest.raises(TypeError) as refusal:

        @tlaw.law
        def mixed_choice_is_not_all_integers(value: MixedChoice):
            return True

    assert "int/str" in str(refusal.value)


def test_an_uncheckable_protocol_base_is_left_unproven() -> None:
    @tlaw.law
    def protocol_contract_is_not_guessed(value: ProtocolChoice):
        return value.value == 1

    protocol_contract_is_not_guessed()
