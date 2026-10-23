Running `tlaw check` no longer writes `.hypothesis` or `__pycache__` caches into the checked project, and it pins `PYTHONHASHSEED=0` so retained counterexamples replay deterministically.
