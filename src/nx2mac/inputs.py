"""Game file extensions shared by input validation and staging."""

#: Switch 1 container extensions the engine script understands.
SWITCH1_EXTENSIONS: frozenset[str] = frozenset({".nsp", ".nsz", ".xci", ".xcz"})
#: A Nintendo Update Package file (Switch 2 / NSO game).
NSO_EXTENSION: str = ".nup"
#: Filenames we recognise as a console's key set, in lookup order.
KEYS_FILENAMES: tuple[str, ...] = ("prod.keys", "prod_keys.txt")
#: Everything we treat as a packable game payload.
GAME_EXTENSIONS: frozenset[str] = SWITCH1_EXTENSIONS | {NSO_EXTENSION}
