# Per-game fallback icons

Drop an icon here (or in `~/.config/nx2mac/icons/`) named after the game's title ID — 16 hex
digits, e.g. `04007EF01F8AE000.png` — and a build of that game that carries no icon of its own
takes it. PNG, JPEG accepted, any size.

Resolution order for a game with no icon:
1. `~/.config/nx2mac/icons/<TITLE ID>.png|.jpg`   (the user's own)
2. `resources/icons/<TITLE ID>.png|.jpg`          (shipped with nx2mac)
3. `resources/app-icon.png`                       (nx2mac's own, the generic stand-in)
