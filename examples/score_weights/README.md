# Law-first feature development

This folder demonstrates starting a **new feature** with tlaw: the laws
were written before the implementation, and the folder's flat shape —
`implementation.py` beside `laws.py` — makes it mutation-testable by
construction. Every output below is real; rerun the commands to verify.

## The workflow

### 1. Write the laws first

Before any implementation existed, `laws.py` stated what score
normalization must do, in application vocabulary:

- weights sum to one (`tlaw.same(..., within=1e-9)` — an explicit
  absolute tolerance, because exact float equality would reject correct
  code);
- weights stay in `[0, 1]`;
- the largest score gets the largest weight; and
- shifting every score by one constant changes nothing.

The input domain is part of the contract and visible in the signature:

```python
Scores = tlaw.list_of(tlaw.number(-10, 10), min_size=1, max_size=8)
```

### 2. Watch them fail

With an empty `normalize_scores`, `tlaw check laws.py` fails on every
law with a shrunk counterexample. A law that has never failed has never
demonstrated it can.

### 3. Implement until green

```console
$ tlaw check laws.py
4 passed
```

Use `--explain` to see exactly what was generated rather than taking it
on trust — each law reports its domains, example budget, and
deterministic posture.

### 4. Measure the laws' strength

Passing laws are not yet evidence that the laws constrain anything.
`tlaw mutate` challenges them with changes nobody chose:

```console
$ tlaw mutate laws.py implementation.py
Canary: KILLED (laws observe the mutated module)
Mutations:
- 5e5bc888f33b SURVIVED normalize_scores: replace score - largest with score + largest (all laws passed)
- 84c445060320 KILLED normalize_scores: replace value / total with value * total (law: laws.py::weights_sum_to_one, laws.py::weights_stay_between_zero_and_one)
Killed 1 of 2 runnable generated mutations.
Survivors: 1
```

The canary line is the default wiring guard: before scoring, tlaw runs a
poisoned implementation in which every function raises. Because
`laws.py` imports `from implementation import ...` — the sibling module
by name — the poison is observed and killed. Laws that import an
installed package instead would report INVALID EVIDENCE here rather
than a page of meaningless survivors.

### 5. Investigate the survivor

The surviving mutant replaces `score - largest` with `score + largest`.
Investigation shows it is a **true equivalent within the declared
domain**: softmax is invariant under any constant shift of its inputs,
so adding the maximum computes the same weights as subtracting it. The
two differ only in numerical range, and scores in `(-10, 10)` keep
`exp()` far from overflow either way.

That conclusion is a trust decision, so it is committed as ordinary
reviewable data in `equivalents.toml`, keyed by the mutant's
content-based ID (stable across unrelated edits):

```toml
[equivalent.5e5bc888f33b]
reason = "softmax shift-invariance: +max equals -max within the declared domain"
```

### 6. Complete evidence

```console
$ tlaw mutate laws.py implementation.py --equivalents equivalents.toml
- 5e5bc888f33b UNRESOLVED EQUIVALENT ... (declared equivalent: softmax shift-invariance ...)
- 84c445060320 KILLED ...
Killed 1 of 2 runnable generated mutations.
Survivors: 0
```

Exit code `0`: every mutant is killed or explained, and the explained
one stays visible in the report and the denominator forever. If a later
edit makes the ledger entry wrong — the mutant starts dying, or the
function changes and the ID disappears — the entry is reported as stale
instead of being silently honored.

## Why the flat folder matters

`tlaw check` works anywhere: laws may import installed packages, live in
any directory, and pin any importable code. `tlaw mutate` is stricter:
its boundary is one directory, copied whole for every isolated run, and
the laws must import the implementation by its module name so the
mutated copy is what runs.

Starting a feature as `feature/implementation.py + laws.py` therefore
gives you the strength meter for free from day one. Package-shaped code
gets `check` today and waits for multi-module mutation boundaries.
