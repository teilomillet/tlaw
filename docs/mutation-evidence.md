# Generated mutation evidence

Status: required before derived project-wide confidence views. The first
mutation boundary is implemented as `tlaw mutate laws.py implementation.py`
in `tlaw/mutation.py`; the rules below are encoded there and pinned by
`tests/test_mutation_properties.py` and `tests/test_mutation_engine.py`.

## Why it is load-bearing

The beginner interface makes executable laws intentionally cheap to write. It
also makes weak laws cheap to write:

```python
@tlaw.law
def returns_a_list(values: list[int]):
    return isinstance(transform(values), list)
```

That statement may be true and useful, but its passing result says little about
the values inside the list. A graph or contract card that presents it as broad
protection would manufacture confidence.

tlaw therefore needs challenges that were not selected by the law author. The
initial declared known-wrong audit proves the runner and report semantics, but
its score is self-graded evidence. Generated mutations are the counterweight
for the loose authoring form. The first implementation now exists; broader
pipeline graphs, contract cards, or impact reports still must not display more
strength than that narrow evidence supports.

## Evidence loop

For one frozen implementation and law suite:

1. verify that the unmodified implementation passes with a nonempty, complete
   inventory of passed tlaw law obligations, and measure its
   duration — that measurement calibrates every mutant's timeout budget;
2. generate deterministic, reproducible source changes within the declared
   implementation boundary;
3. record each mutation as a stable ID and readable code diff;
4. kill the canary before scoring anything: a poisoned implementation in
   which every function raises must be rejected by the laws, or the laws
   never observed the boundary module and the report is INVALID EVIDENCE;
5. reject mutations that cannot be built or loaded without counting them as
   law successes;
6. run the unchanged laws against every runnable mutation in a fresh copy of
   the boundary directory and require the same completed law inventory;
7. retain the failed law identities, typed inputs, and exception fingerprints;
8. replay those exact failures against the same mutant in a fresh copy, or
   confirm a timeout after a fresh complete baseline recalibrates its budget;
   an unconfirmed candidate is an error, not a kill; and
9. show every surviving mutation rather than hiding it behind one percentage.

The canary is a default with no opt-out. The failure it prevents is silent
and flattering-looking in reverse: laws that import an installed package
instead of the sibling module would let every mutant "survive", and the
report would read as weak laws when the truth is unwired evidence. The
person the canary protects is the one who has never heard of it.

A new or changed law provides stronger evidence when it rejects a relevant
mutation that previously survived. The generated mutation remains evidence
configuration; the committed executable law remains the source of behavioral
truth.

## Honest outcomes

### Exhaustive finite inputs

Mutation uses sampled checks by default. To enumerate the declared finite
inputs in every ordinary run, select exhaustive mode explicitly:

```console
tlaw mutate laws.py implementation.py --exhaustive --max-cases 1000 --json mutations.json
```

The Python API accepts `mutate(laws, implementation, exhaustive=True,
max_cases=1000)`. The default cap is 1,000 combinations per collected law case,
including each pytest parametrization. The supported inputs and enumeration
order are the same as [exhaustive checking](current-behavior.md#exhaustive-finite-checking):
finite scalar domains, dictionary-form records, and explicitly bounded lists,
including nesting. Unsupported declarations or a space larger than the cap
invalidate the baseline; tlaw never falls back to sampling.

The baseline, canary, mutants, fresh timeout baseline and timeout retry all use
the selected mode and cap. Ordinary receipts must match the baseline's law
inventory, checking method, total case counts and list length bounds. A passing
baseline or survivor must exhaust every declared case. A failing law stops at
its first counterexample, so its checked count may be smaller than its total.
Exact replay executes only that retained input and checks the saved exception;
it does not enumerate the domain again.

Text reports show the checking method, cap, and each law case's checked/total
counts and nested list length bounds. JSON adds `method` (`sampled` or
`exhaustive`) and `max_cases` (an integer or `null`) at the top level; the
baseline, canary and per-mutant obligations retain their counts, bounds and
`exhausted` flags. A timed-out process may leave no receipt, so its counts remain
unavailable. Timeout kills still require a fresh passing baseline and a repeated
calibrated timeout, and carry no law attribution.

Exhaustion covers the declared inputs for these generated mutants. It does not
establish correctness outside those bounds or against every possible mistake.

### Outcome meanings

Every generated mutation needs one explicit outcome:

- **killed:** an **observed** kill has complete law execution and retained
  counterexamples that reproduce exactly; a **timeout** kill repeats its timeout
  after a fresh complete baseline recalibrates the budget and names no law;
- **survived:** the same complete law inventory passed against the mutant
  after passing against the baseline;
- **invalid:** the mutation could not build or load — the baseline collected
  these laws, so a collection failure under a mutant is a load failure,
  never a kill and never a law success;
- **error:** execution evidence was missing, incomplete, or inconsistent;
  a refutation could not be retained; or confirmation failed to reproduce the
  exact law failure or the calibrated timeout; or
- **unresolved equivalent:** a survivor whose committed ledger entry explains
  why the change is not observable within the declared boundary.

Invalid and error outcomes are not kills. Equivalent mutations are generally
undecidable automatically; a survivor is a prompt for investigation, not proof
that the laws are weak.

An exception and an infinite loop are behavior, not infrastructure: only a
timeout with no measured baseline to calibrate it would be an error, and the
engine always measures the baseline first.

### Why a kill's kind is load-bearing

A timeout and a law failure are different claims, so neither may confirm the
other on replay. Two runs that disagree about what happened are not two runs
agreeing that something happened.

The calibration itself has to be fresh. A budget derived from a baseline
measured before the mutant loop began can be hours old on a large run; if the
machine became contended in the meantime, every remaining mutant looks slow
and two slow runs would confirm each other with no law involved. A timeout
kill therefore re-measures the baseline immediately before its replay and is
timed against *that* budget. A baseline that no longer passes leaves no
calibration at all, which is an error rather than a kill.

Attribution replays too. Which law defends a behavior — the reading
[`law-first-experiment.md`](law-first-experiment.md) calls the strongest thing
a mutation report produces — is taken from the killing law names, so two runs
that name different laws leave that reading unsupported even though both said
"killed". Only a confirmed observed kill ever carries law names; a downgraded
run keeps its first run's attribution in its note, as history rather than as a
law that rejected the mutant.

Ordinary pytest tests still run, but cannot supply tlaw law obligations or a
retained law failure. Skips, xfails, setup/teardown failures, and changes to the
collected law cases prevent ordinary outcome attribution. Exact replay runs
only the retained inputs and verifies the same exception type and message;
rerunning generation and finding another failure is not confirmation.

The `tlaw.mutation/v1` JSON report retains the original per-mutant `evidence`
and fresh `replay_evidence`, plus `replay_method` (`exact` or `timeout`) and the
confirmation process's exit code, timeout flag, and duration. Timeout attempts
can lack a completed law receipt; that missing observation is left unavailable.
The method describes the attempt, while the outcome and note describe whether
it confirmed a kill. A passed exact replay receipt records reproduction of the
saved failure, not a passing original implementation.

The mandatory canary also needs complete matching obligations and an actual
law failure for an ordinary wiring result. A canary refutation need not be
encodable to prove the poisoned implementation was observed. The calibrated
timeout path remains separate. The boundary copies and package shadowing rules
are unchanged.

The truthful strength statement is narrow:

```text
Killed 17 of 21 runnable generated mutations.
Kills by calibrated timeout (no law attribution): 2
Survivors: 3
Unresolved equivalent candidates: 1
Invalid or infrastructure errors: 2
```

It must not be shortened to "the module is 81% correct" or "81% covered." The
timeout line is stated separately for the same reason: those two kills are
real behavioral differences, but they name no law and can never appear in the
attribution reading, so the numerator stays decomposable instead of quietly
borrowing weight from them.

## The equivalence ledger is law-space

Marking a survivor as equivalent is a trust decision, so it follows the same
rule as laws: written down and committed means accepted. The ledger is an
ordinary TOML file passed with `--equivalents`:

```toml
version = 1

[equivalent.3e67f984ab5f]
reason = "invisible to the declared length-only observation"
```

Three properties keep the ledger from becoming the new way to game the score:

- mutation IDs are content-based — module, function, in-function position,
  operator, and the before/after code — never line numbers, so entries
  survive unrelated edits and rot only when the mutated function itself
  changes;
- the ledger explains survivors only; an entry whose mutant was killed,
  invalid, errored, or does not exist is reported as stale instead of being
  silently honored or dropped, and unresolved equivalents never leave the
  report or the runnable denominator; and
- changes to this file belong in the same review scope as law changes.
  Current `tlaw diff` covers executable law functions only; surfacing an
  equivalence-ledger edit beside them remains an explicit limitation.

## Current mutation boundary

The implemented command accepts one `laws.py` and one different implementation
module. For a flat module, both files share a directory and tlaw copies that
whole directory. For a module inside a regular or namespace package, the laws
may live anywhere: tlaw copies the whole package boundary, runs from a neutral
directory, and places the copy before both the laws directory and installed
package on the import path. The namespace boundary comes from the dotted import
in the laws, including when a regular child package sits under a namespace
parent. The mandatory canary proves that shadowing worked before mutants are
scored. Every baseline, mutant, and kill replay gets a fresh copy. Mutation is
limited to Python function and method bodies, including nested functions and
methods; compiled modules and mutating more than one package in the same run
are not implemented. Laws may still import other, unmutated packages normally.

The current deterministic single-site operators are:

- swap `+` with `-`, `-` with `+`, `*` with `/`, or `/` with `*`;
- change `<` to `<=`, `<=` to `<`, `>` to `>=`, or `>=` to `>`;
- flip a returned boolean or add one to a returned numeric constant;
- replace a standalone call statement with `pass`; and
- negate an `if` or `while` condition.

Each mutation should change one thing. Broad model-generated rewrites would be
hard to attribute, reproduce, and explain, so they are not the first evidence
source.

The mutation engine must not require an LLM. An LLM may later suggest additional
mutations through the same recorded-diff interface.

Cost is deliberately unresolved. Mutants times laws times examples multiplies
quickly, and the known answers — run the likeliest-killing law first, budget
per mutant — can wait. What must not happen is solving cost by silently
reducing examples or skipping replays: any budget cut is a visible setting,
never a quiet default.

## Gate before views

Derived views may be implemented experimentally, but they must not display a
boundary as protected until its evidence includes independently generated
challenges. The view must distinguish:

- a law exists;
- the current implementation passes;
- generated mutations were challenged;
- relevant mutations survived; and
- behavior remains unknown.
