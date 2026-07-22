# Change fragments

`CHANGELOG.md` is release output. During development, describe each
user-visible change in one small file in this directory instead of editing the
changelog directly.

Name a fragment like this:

```text
+short-description.category.md
```

The leading `+` means no GitHub issue number is required. Valid categories are:

- `added` for a new capability;
- `changed` for changed behavior;
- `deprecated` for behavior that will be removed;
- `removed` for removed behavior;
- `fixed` for a bug fix;
- `security` for a security fix; and
- `limitations` for an important boundary users must know.

Write one user-facing sentence without a bullet prefix:

```text
Add `tlaw.integer(low, high)` for bounded integer inputs.
```

Preview all pending entries without changing files:

```console
uv run towncrier build --draft
```

The complete contribution and release rules are in `CONTRIBUTING.md`.
