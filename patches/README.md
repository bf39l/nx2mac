# nx2mac engine patches

Git-format patch files applied to the pinned nxnative checkout **after** every
fetch + `git checkout -f <commit>` in `nx2mac`. This lets us ship fixes/workarounds
for upstream engine code (or anything vendored inside its tree — dependencies,
tools) until they land upstream.

## Conventions

- One fix per patch: `NNNN-short-description.patch` (`0001-`, `0002-`, …).
  Files are applied in **filename order**, so the number prefix controls it. A
  patch may span a couple of files when they are one feature (e.g. the account
  picker's two files). Keep related launcher edits in one patch rather than
  stacking several on the same file.
- The current series: `0001` launcher state dirs (read the bundled env, resolve
  relative paths with a read-only fallback, player env overrides), `0002`
  build-script state-dir report, `0003` account user picker.
- Diffs are produced against a pristine upstream tree:
  `git diff --relative > patches/NNNN-name.patch` (or `--binary` when needed).
- Rebase or drop a patch once its fix lands upstream; keep the series minimal.
- A failing patch aborts the build with an error naming the file — don't rename
  shipped patches carelessly without checking references/tests. `library.py`
  detects the relocatable launcher by the *relocatable* word in the patch name,
  not its number.

Because `nx2mac` resets the working tree on every run before applying, the series
is re-applied from scratch each time: same pinned commit + same series = same tree.
