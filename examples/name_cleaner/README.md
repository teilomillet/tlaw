# A first tlaw folder: cleaning names

This folder is a complete small feature. You can read it without searching for
a helper or configuration file elsewhere:

- [`name_cleaner.py`](name_cleaner.py) is the code people use;
- [`laws.py`](laws.py) is the behavior that code must keep; and
- this file explains how to run and change both.

The feature turns a name such as `"  ADA   Lovelace "` into
`"ada lovelace"`. It also cleans a list of names while keeping their order.

## Run it in an IDE

You need [Python 3.12 or later](https://www.python.org/downloads/) and
[uv](https://docs.astral.sh/uv/getting-started/installation/). Running
`uv sync` creates the project environment and installs tlaw's dependencies from
this repository.

1. Open the tlaw repository as a folder in your IDE.
2. Open the IDE terminal at the repository root and run `uv sync` once.
3. Select `.venv/bin/python` as the IDE's Python interpreter. On Windows,
   select `.venv\\Scripts\\python.exe`.
4. Run:

   ```console
   uv run tlaw check examples/name_cleaner/laws.py --explain
   ```

A successful run ends with `4 passed`. The `--explain` section also says that
tlaw generated strings and lists of strings, tried up to 100 examples for each
law, and used a deterministic run with no example database.

If tlaw is installed in another project and this folder is inside that project,
open a terminal in this folder and run `tlaw check laws.py --explain` instead.

## Read the implementation

Start with this definition from `name_cleaner.py`:

```python
def clean_name(name: str) -> str:
```

It says:

- `def` starts a function definition;
- `clean_name` is the function's name;
- `name` is the input;
- `: str` says the input is text; and
- `-> str` says the returned value is also text.

The body then uses only a list, a `for` loop, and three standard string
operations. [`split()`](https://docs.python.org/3.12/library/stdtypes.html#str.split)
finds the words,
[`lower()`](https://docs.python.org/3.12/library/stdtypes.html#str.lower)
lowercases each word, and
[`" ".join(...)`](https://docs.python.org/3.12/library/stdtypes.html#str.join)
reconnects them with one space.

Python's official tutorial introduces
[`def` and function definitions](https://docs.python.org/3.12/tutorial/controlflow.html#defining-functions),
and its standard-library reference explains
[type annotations](https://docs.python.org/3.12/library/typing.html). The
annotations help the IDE, but tlaw also uses them to decide what example inputs
to generate.

## Read the first law

```python
@tlaw.law
def cleaning_a_name_follows_the_written_steps(name: str) -> bool:
    expected_words = []

    for word in name.split():
        expected_words.append(word.lower())

    expected_name = " ".join(expected_words)
    return clean_name(name) == expected_name
```

Read it from top to bottom:

- `@tlaw.law` says that the function below is behavior to enforce;
- `name: str` asks tlaw for many text inputs;
- `-> bool` says the law returns either `True` or `False`;
- the loop writes down the expected behavior without asking `clean_name` for
  the answer; and
- the final line returns one Python boolean: either the real and expected
  results are equal, or they are not.

The few repeated lines are deliberate. If a law used `clean_name(name)` to
calculate both the actual and expected answer, the same bug could appear on
both sides and pass. Here, the law itself remains readable executable Python
and is the source of truth.

Underneath `@tlaw.law`, tlaw uses Hypothesis. Hypothesis's official
[`@given` reference](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.given)
documents how type annotations can supply generated arguments, while its
[introductory tutorial](https://hypothesis.readthedocs.io/en/latest/tutorial/introduction.html)
explains that it tries many examples and reduces a failure to a small example.
You do not need to write Hypothesis strategy code for this folder.

The other three laws state different boundaries:

- cleaning already-clean text again must not change it;
- whitespace added around a name must not change the result; and
- cleaning a list must agree with cleaning every item, without skipping or
  reordering one.

## See the contract protect the code

Temporarily change the last line of `clean_name` from:

```python
return " ".join(cleaned_words)
```

to this incorrect shortcut:

```python
return name.lower()
```

Run the same `tlaw check` command. The first law fails because the shortcut no
longer removes extra whitespace. Hypothesis reports a small input that shows
the disagreement. Put the original line back and all four laws pass again.

This is the smallest working agreement between a person and a coder:

1. change or add an executable law to state the intended behavior;
2. change the implementation until the laws pass;
3. review the law as ordinary code; and
4. commit both, then run the same command in CI once the repository has a
   workflow.

No language model is required for any of these steps.

## Write one law yourself

Add this law to the bottom of `laws.py`:

```python
@tlaw.law
def cleaning_keeps_the_number_of_names(names: list[str]) -> bool:
    return len(clean_names(names)) == len(names)
```

Run the check again. It should now end with `5 passed`. Then temporarily change
the last line of `clean_names` from `return cleaned_names` to
`return cleaned_names[:-1]`. Your new law catches the missing final name.

This added law is useful but not sufficient by itself: a function returning the
right number of empty strings would satisfy it. A green check means only that
the current implementation satisfied the written laws for the generated
examples.

## Challenge the laws with generated mistakes

tlaw can now change one implementation file in small, mechanical ways and ask
whether the unchanged laws notice:

```console
uv run tlaw mutate \
  examples/name_cleaner/laws.py \
  examples/name_cleaner/name_cleaner.py
```

For the code in this folder, the current result is:

```text
Killed 2 of 2 runnable generated mutations.
Survivors: 0
Unresolved equivalent candidates: 0
Invalid or infrastructure errors: 0
```

One mutation removes the call that adds a cleaned word; the first law rejects
it. The other removes the call that adds a cleaned name; the list law rejects
it. Every kill is rerun once before tlaw counts it.

This remains narrow evidence. It says the laws rejected the two changes this
mutation engine generated for this implementation; it does not say the feature
is 100% correct or that every possible mistake was considered. If a mutation
survives, tlaw prints it instead of hiding it behind a score.

## Current limits of this easy form

Keep first laws pure: return a value instead of changing the input object. tlaw
checks for detectable input mutation, but that guard is not a proof of purity.
This example uses ordinary Python booleans and exact equality because strings
need nothing more. For numerical work, tlaw now provides `number`, `list_of`,
and `same(..., within=...)`; the exact semantics and remaining limitations are
listed in [current tlaw behavior](../../docs/current-behavior.md).
