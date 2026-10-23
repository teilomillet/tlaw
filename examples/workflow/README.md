# Check, retain, replay, challenge

`implementation.py` increments an integer. `laws.py` requires exactly one step
for inputs from `0` through `2`. Run these commands inside this folder with
`tlaw` installed in your active environment (`uv add --dev tlaw` in a project,
then prefix the commands with `uv run`).

```python
# implementation.py
def increment(value: int) -> int:
    return value + 1
```

```python
# laws.py
import tlaw
from implementation import increment

Count = tlaw.integer(0, 2)

@tlaw.law
def increment_advances_once(value: Count):
    return increment(value) == value + 1
```

Check the correct implementation and save its evidence:

```console
tlaw check laws.py --json passing.json
tlaw check laws.py --exhaustive --max-cases 3 --json exhaustive.json
```

Both exit `0`. The first report labels the check `sampled`; the second records
`3/3 cases checked` and `exhausted: true`. That covers this declared finite
domain, rather than every Python integer.

Now keep a copy of the correct implementation and substitute the supplied
known-wrong version, which returns its input unchanged. Leave the law intact:

```console
cp implementation.py implementation.py.good
cp implementation_wrong.py implementation.py
tlaw check laws.py --json failure.json
```

The check exits `1` and retains a counterexample in `failure.json`. Its
`failures` entry includes typed inputs and the exception; `obligations` records
which law ran and failed. A JSON request exits `2` if the evidence cannot be
retained, so a file-writing or collection error does not count as a refutation.

Replay the retained input without generating new examples:

```console
tlaw check --replay failure.json
```

Replay exits `0` when it reproduces the saved failure, including its exception
type and message. This means the old failure is reproducible; the implementation
still fails its law.

Restore the correct code, then try the same replay:

```console
mv implementation.py.good implementation.py
tlaw check --replay failure.json
tlaw check laws.py
tlaw mutate laws.py implementation.py --json mutation.json
tlaw mutate laws.py implementation.py --exhaustive --max-cases 3 --json finite-mutation.json
```

Replay now exits `1` because the retained counterexample no longer fails. The
fresh check exits `0`. Mutation also exits `0`: its baseline passes, its canary
confirms the laws observe the copied implementation, and its one generated
mutant (`+` becomes `-`) is killed and confirmed by exact replay. `mutation.json`
keeps the original failure and replay evidence. This command uses sampled checks.
The second mutation command explicitly selects exhaustive checking: its baseline
reports `3/3 cases checked`, while its canary and mutant each fail on the first
case (`1/3`, not exhausted). The mutant's exact replay checks only the retained
input (`1/1`), without enumerating again. Both campaigns keep fresh isolated
copies, the canary and calibrated timeout confirmation.

The installed-package gate runs this sequence in a temporary copy of the folder,
using a wheel installed in a fresh environment. It checks the JSON evidence and
expected exit codes, and leaves the checked-in example unchanged.
