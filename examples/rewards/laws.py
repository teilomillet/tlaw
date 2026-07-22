from __future__ import annotations

from typing import Annotated

import tlaw

from rewards import center


type Reward = Annotated[float, tlaw.number(0, 1)]
type Shift = Annotated[float, tlaw.number(-1, 1)]


@tlaw.law
def centered_rewards_sum_to_zero(rewards: list[Reward]) -> tlaw.Comparison:
    return tlaw.same(sum(center(rewards)), 0.0, within=1e-12)


@tlaw.law
def shifting_every_reward_changes_nothing(
    rewards: list[Reward],
    amount: Shift,
) -> tlaw.Comparison:
    shifted = [reward + amount for reward in rewards]
    return tlaw.same(center(shifted), center(rewards), within=1e-12)
