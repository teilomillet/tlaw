# Domains and approximate comparison

This folder demonstrates two current tlaw features without Hypothesis strategy
code:

- `Annotated[type, domain]` states which values tlaw should generate while
  keeping an ordinary type visible to an IDE; and
- `tlaw.same(..., within=...)` compares nested values with an explicit absolute
  tolerance and retains the first difference when a law fails.

Everything for the example is in this folder:

- [`rewards.py`](rewards.py) contains the ordinary implementation; and
- [`laws.py`](laws.py) contains its executable behavior.

Run it from the repository root:

```console
uv run tlaw check examples/rewards/laws.py --explain
```

The domains are declared once near the laws:

```python
type Reward = Annotated[float, tlaw.number(0, 1)]
type Shift = Annotated[float, tlaw.number(-1, 1)]
```

The base type tells the reader and type checker that these are floats. The tlaw
domain generates finite values between the inclusive bounds. tlaw currently
trusts the author to keep those two parts consistent; it does not reject a base
type that disagrees with its domain.

The first law reads:

```python
@tlaw.law
def centered_rewards_sum_to_zero(rewards: list[Reward]) -> tlaw.Comparison:
    return tlaw.same(sum(center(rewards)), 0.0, within=1e-12)
```

`within=1e-12` is absolute tolerance, not a percentage. Every numeric leaf may
differ by at most that amount. Removing `within` asks for exact comparison.

The second law says that adding the same finite shift to every reward must not
change the centered result. Returning `tlaw.same(...)` directly lets a failure
show both lists and the first index where they differ.

Current limitation: `--explain` prints an alias such as `list[Reward]` instead
of expanding the attached bounds. The bounds remain visible in this file. See
[current tlaw behavior](../../docs/current-behavior.md) for every supported
shape and the remaining limits.
