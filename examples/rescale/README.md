# Relative tolerance: a numerical-stability refactor

This example exists for one feature — `tlaw.same(..., relative=...)` — and
for the situation that motivates it: a numerical refactor whose correctness
can only be stated to *relative* precision. Every number below is real.

## The refactor

`total_naive` accumulates a running sum directly; `total_kahan` replaces it
with compensated (Kahan) summation to reduce accumulated rounding. The two
are mathematically equal, so the refactor must preserve results.

But "preserve" cannot mean an exact match, and it cannot mean a fixed
absolute tolerance either. For well-conditioned inputs the two sums agree
to a few parts in 10^15 — yet at magnitude that agreement is an absolute
gap far larger than any small `within`:

```text
naive = 5000001499.499997   kahan = 5000001499.5
gap   = 2.86e-06
absolute within=1e-6 passes? False   <- rejects a correct refactor
relative 1e-9  passes?       True    <- correct
```

A single absolute tolerance cannot fit inputs whose magnitude spans nine
orders: set it tight and it rejects correct code at scale; set it loose and
it passes wrong code near zero. Relative tolerance states the real
contract — *equal to a few parts per billion, whatever the scale*:

```python
@tlaw.law
def kahan_matches_naive_to_relative_precision(values: Values):
    return tlaw.same(total_kahan(values), total_naive(values), relative=1e-9)
```

## What mutation testing found

```console
$ tlaw mutate laws.py implementation.py
Killed 3 of 4 runnable generated mutations.
Survivors: 1
```

The three kills are the structural mutations of the Kahan step. The one
survivor is worth reading:

```text
- e77faa4243e4 SURVIVED total_kahan: replace value - compensation with value + compensation
```

Flipping the compensation's sign changes the result only at the ~1e-15
relative level — **below the declared 1e-9 tolerance**. The compensation
term lives beneath the floor the law sets, so no input in the domain can
tell correct compensation from sign-flipped compensation. It is a true
equivalent *within this contract*; killing it would require comparing
against an exactly-rounded reference (`math.fsum`) at a tolerance near
machine epsilon, which is a different, tighter law than "the refactor
preserved results to 1e-9".

That reasoning is a trust decision, so it is committed in
`equivalents.toml`, and the run then completes cleanly:

```console
$ tlaw mutate laws.py implementation.py --equivalents equivalents.toml
Killed 3 of 4 runnable generated mutations.
Survivors: 0
Unresolved equivalent candidates: 1
```

The survivor stays visible in the report and the denominator forever; if a
later edit makes the ledger entry wrong, it is reported as stale rather
than silently honored.
