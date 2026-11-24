# nx2mac engine patches

Git-format patch files applied to the pinned nxnative checkout **after** every
fetch + `git checkout -f <commit>` in `nx2mac`. This lets us ship fixes/workarounds
for upstream engine code (or anything vendored inside its tree — dependencies,
tools) until they land upstream.

## Conventions

- One file per fix: `NNNN-short-description.patch` (`0001-`, `0002-`, …).
  Files are applied in **filename order**, so the number prefix controls it.
- Diffs are produced against a pristine upstream tree:
  `git diff --relative > patches/NNNN-name.patch` (or `--binary` when needed).
- Rebase or drop a patch once its fix lands upstream; keep the series minimal.
- A failing patch aborts the build with an error naming the file — don't rename
  shipped patches carelessly without checking references/tests.

Because `nx2mac` resets the working tree on every run before applying, the series
is re-applied from scratch each time: same pinned commit + same series = same tree.
