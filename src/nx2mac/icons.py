"""Game icons: known-title key art downloaded on demand and center-cropped to a square.

A Switch 2 NUP (or a dump folder) carries no icon of its own, so the engine would leave the app
icon blank. nx2mac ships a small map of title ID → verified SteamGridDB key art (NX2Apple's
``switch2Fallbacks`` approach) and downloads + center-crops it into the game's library folder at
build time; the engine's ``--fallback-icon`` then picks it up. No binary art is shipped in the repo.
"""

import urllib.request
from pathlib import Path

from .runner import Env

#: Title ID → verified 16:9 key art on SteamGridDB's CDN (NX2Apple's switch2Fallbacks).
KNOWN_ICON_URLS: dict[str, str] = {
    "0400245021D2C000": "https://cdn2.steamgriddb.com/grid/4019460f699df3ca57bb36a6cc8b1ce5.png",  # Yoshi and the Mysterious Book
    "0400380020056000": "https://cdn2.steamgriddb.com/grid/220a08c045f283231e519ed83cd21466.png",  # Splatoon Raiders
    "04003F40232DE000": "https://cdn2.steamgriddb.com/grid/30430599d3640690ebddef63609c5f31.png",  # Hyrule Warriors: Age of Imprisonment
    "0400486024DEC000": "https://cdn2.steamgriddb.com/grid/925526bdfb5910517494acb4db2537d5.png",  # The Legend of Zelda: Ocarina of Time
    "04007EF01F8AE000": "https://cdn2.steamgriddb.com/grid/f73cc1e0813d90b30a22ca9af2e9c8b0.png",  # Fire Emblem: Fortune's Weave
    "0400A3A01EE4C000": "https://cdn2.steamgriddb.com/grid/00b97e285bef52b2826cfbeecd4d30f7.png",  # Pokémon Pokopia
    "0400D4201C6EA000": "https://cdn2.steamgriddb.com/grid/eb3353d1afeb0b6b1c6507bb5d39c757.png",  # Mario Tennis Fever
    "0400E88025B1A000": "https://cdn2.steamgriddb.com/grid/a82908cc940793dc1d63acfc0218b2f9.jpg",  # Star Fox
}

_USER_AGENT = "nx2mac (macOS)"


def _is_image(data: bytes) -> bool:
    """A PNG or JPEG, by magic bytes."""
    return data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))


def _center_crop_square(env: Env, src: Path, dest: Path) -> bool:
    """Center-crop ``src`` to a square at ``dest`` via ``sips``; a square passes through unchanged.

    ``sips`` is macOS-only (nx2mac is macOS-only). ``--cropOffset`` is ``offsetY offsetX``.
    """
    probe = env.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(src)])
    dims: dict[str, int] = {}
    for line in probe.stdout.splitlines():
        key, _, value = line.partition(":")
        if key.strip() in ("pixelWidth", "pixelHeight"):
            try:
                dims[key.strip()] = int(value.strip())
            except ValueError:
                pass
    if "pixelWidth" not in dims or "pixelHeight" not in dims:
        # sips could not read it: keep the raw image (still a valid icon).
        src.replace(dest)
        return dest.is_file()
    width, height = dims["pixelWidth"], dims["pixelHeight"]
    if width == height:
        src.replace(dest)
        return True
    side = min(width, height)
    result = env.run(
        [
            "sips",
            "-c",
            str(side),
            str(side),
            "--cropOffset",
            str((height - side) // 2),
            str((width - side) // 2),
            str(src),
            "--out",
            str(dest),
        ]
    )
    return result.returncode == 0 and dest.is_file()


def download_icon(title_id: str, dest: Path, env: Env, timeout: float = 20.0) -> bool:
    """Download the known key art for ``title_id``, center-crop it to a square, and write ``dest``.

    False when the title is unknown, the host is unreachable, or the bytes are not an image.
    """
    url = KNOWN_ICON_URLS.get(title_id.upper())
    if url is None:
        return False
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except OSError:
        return False
    if not data or not _is_image(data):
        return False
    raw = dest.with_name(dest.name + ".download")
    raw.write_bytes(data)
    try:
        return _center_crop_square(env, raw, dest)
    finally:
        raw.unlink(missing_ok=True)


def ensure_game_icon(game_folder: Path | None, title_id: str | None, env: Env) -> Path | None:
    """Make sure ``<game_folder>/<TITLE ID>.png`` exists, downloading the known art when it does not.

    A user-dropped icon always wins and is never overwritten.
    """
    if title_id is None or game_folder is None:
        return None
    for suffix in (".png", ".jpg", ".jpeg"):
        for name in (title_id, title_id.lower()):
            candidate = game_folder / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
    dest = game_folder / f"{title_id}.png"
    return dest if download_icon(title_id, dest, env) else None
