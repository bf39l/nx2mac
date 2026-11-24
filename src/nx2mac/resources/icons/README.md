# Per-game fallback icons

A game that carries no icon of its own (a Switch 2 NUP without one, a dump folder) takes a fallback
icon, resolved in this order:

1. `<library>/nx2mac_game_lib/<TITLE ID>/<title id>.png|.jpg`   — drop it beside the game's data
2. `resources/icons/<TITLE ID>.png|.jpg`                        — shipped with nx2mac
3. `resources/app-icon.png`                                     — nx2mac's own, the generic stand-in

The title ID is 16 hex digits (e.g. `04007EF01F8AE000`); matching is case-insensitive. PNG, JPEG,
any size.
