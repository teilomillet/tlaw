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

1. verify that the unmodified implementation passes, and measure its
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
   the boundary directory;
7. record the exact law and minimized input that rejects each mutation;
8. rerun every kill once — a kill that does not replay is an error, not a
   kill; and
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

Every generated mutation needs one explicit outcome:

- **killed:** the mutant behaved differently from the passing baseline — a
  law compared false, the mutated implementation raised an exception, or the
  run exceeded a timeout calibrated against the measured baseline duration;
- **survived:** all laws passed, and the kill-or-survive attribution is
  trusted because the same laws passed the unmutated baseline first;
- **invalid:** the mutation could not build or load — the baseline collected
  these laws, so a collection failure under a mutant is a load failure,
  never a kill and never a law success;
- **error:** the harness could not evaluate the mutant, or a kill failed to
  reproduce on its second fresh run — tlaw claims less strength rather than
  counting an unrepeatable failure; or
- **unresolved equivalent:** a survivor whose committed ledger entry explains
  why the change is not observable within the declared boundary.

Invalid and error outcomes are not kills. Equivalent mutations are generally
undecidable automatically; a survivor is a prompt for investigation, not proof
that the laws are weak.

An exception and an infinite loop are behavior, not infrastructure: only a
timeout with no measured baseline to calibrate it would be an error, and the
engine always measures the baseline first.

The truthful strength statement is narrow:

```text
Killed 17 of 21 runnable generated mutations.
Survivors: 3
Unresolved equivalent candidates: 1
Invalid or infrastructure errors: 2
```

It must not be shortened to "the module is 81% correct" or "81% covered."

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
