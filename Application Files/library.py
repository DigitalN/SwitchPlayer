"""The Library folder: its Game folders, what each one's Game app records, and each Game's name.

A Game folder is a subfolder of the Library folder, or a single .nup file in it. Loose .nsp
files and anything else at the top level are ignored. A Game app is `<game name>.app` in its
Game folder, or for a .nup, in the Library folder beside it.

Everything read from a Game app is in nxnative's front-end contract (docs/frontends.md): its
Info.plist. Two details aren't documented yet and are read under ADR 0001's exception, until
its pull request (the fourth in docs/DESIGN.md) is merged, opened yet or not, because a wrong
answer could only change what's shown: the `DMCommit` and `DMChain` keys, which only the Out of date
check uses (nxnative.py), and the name of the `<title ID>_survey` folder, which keeps a build's
survey from being taken for a Game folder. A .nup's title ID, before its first Build, comes from
the container's own header (the NUP format, https://github.com/a55uka/nup, not nxnative's code).
Only reads: nothing here writes to the Library folder.
"""

import functools
import plistlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path

# A Switch game's title ID starts 0100 and a Switch 2 game's 0400; nxnative builds both.
TITLE_ID = r"0[14]00[0-9A-F]{12}"
TAGGED_ID = re.compile(rf"\[({TITLE_ID})\]", re.I)
BUILD_FOLDER = re.compile(rf"^({TITLE_ID})_build$", re.I)
SURVEY_FOLDER = re.compile(rf"^{TITLE_ID}_survey$", re.I)  # undocumented: see DESIGN.md's pull request 4
NXPLAY_FILES = ("start_mac.sh", "nxplay-settings.json")  # NX Play writes these into a build folder; nxnative doesn't
LEADING_ARTICLE = re.compile(r"^the\s+", re.I)


@dataclass
class GameApp:
    path: Path
    title_id: str
    name: str | None  # the game's own name, which nxnative takes from its files
    commit: str | None  # DMCommit: the nxnative checkout it was built from
    chain: str | None  # DMChain: the shader version it was built with
    bundle_id: str | None = None  # CFBundleIdentifier: org.nxnative.<title ID> unless the app was packed with another


@dataclass
class Game:
    id: str  # the Game folder's (or .nup's) name in the Library folder, unique there
    path: Path
    title_id: str | None
    app: GameApp | None
    build_folder: Path | None = None  # <title ID>_build, which NX Play's Builds and nxnative's share

    @property
    def nxplay_build(self):
        """NX Play built this game with nxnative's developer path: its build folder holds the files
        NX Play writes, but there is no Game app to Play until Switch Player builds one."""
        return bool(self.build_folder) and any((self.build_folder / name).is_file() for name in NXPLAY_FILES)

    @property
    def nxplay_save(self):
        path = self.nxplay_save_path()
        return bool(path and path.is_dir())

    def nxplay_save_path(self):
        """NX Play's save for this game, <title ID>_build/run/save, whether or not it exists."""
        if not self.title_id:
            return None
        root = self.path.parent if self.path.suffix.lower() == ".nup" else self.path
        folder = self.build_folder or root / f"{self.title_id}_build"
        return folder / "run" / "save"

    def name(self, titledb_name=None, build_name=None):
        """The game's own name: its Game app's, else the one a Build's `game` event gave it (the
        name its Game app will carry), else the eShop's, else the Game folder's (which the player
        may not have bothered to name well)."""
        if self.app and self.app.name:
            return self.app.name
        return build_name or titledb_name or (self.path.stem if self.path.suffix.lower() == ".nup" else self.path.name)


def is_title_id(value):
    """A Switch or Switch 2 title ID, in either case."""
    return isinstance(value, str) and re.fullmatch(TITLE_ID, value, re.I) is not None


def base_id(tid):
    """A base game's, update's or DLC's title ID → the base game's."""
    value = int(tid, 16)
    low = value & 0xFFF
    if low == 0x800:  # an update
        value -= 0x800
    elif low:  # DLC: the base game's ID + 0x1000 + its index
        value = (value & ~0xFFF) - 0x1000
    return f"{value:016X}"


def title_id_from_names(names):
    """The base game's title ID from [title ID] tags in file names: a base game's own tag
    first, then an update's or DLC's."""
    tags = [m.group(1).upper() for n in names for m in TAGGED_ID.finditer(n)]
    for tid in tags:
        if tid.endswith("000"):
            return tid
    return base_id(tags[0]) if tags else None


# The NUP header, version 1: its superblock, then the table of contents at toc_offset, which
# starts with its own header and one record per package (base game, update, DLC, …).
NUP_SUPER = struct.Struct("<4sHHQQ")  # magic "NUP\x01", header size, -, TOC offset, TOC size
NUP_TOC = struct.Struct("<4sI")  # magic "TOC\x01", package count
NUP_TOC_SIZE, NUP_PACKAGE_SIZE = 0x30, 0x40
NUP_PACKAGE = struct.Struct("<QIIIIB")  # title ID, root, ExeFS, RomFS, version, kind
NUP_KINDS = (0, 1, 2)  # base game, update, DLC: the order to take a title ID from
NUP_MAX_PACKAGES = 4096


def nup_title_id(path):
    """The base game's title ID from a .nup's package table, or None for a file that isn't a
    readable NUP. Only those few hundred bytes are read: no hashes are checked, since a wrong
    answer could only change the art and name shown before the first Build."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return _nup_title_id(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=256)  # by size and modified time too: the Library is scanned every 2 s
def _nup_title_id(path, size, mtime_ns):
    try:
        with open(path, "rb") as f:
            magic, _, _, toc_offset, toc_size = NUP_SUPER.unpack(f.read(NUP_SUPER.size))
            if magic != b"NUP\x01" or toc_offset + toc_size > size:
                return None
            f.seek(toc_offset)
            toc_magic, count = NUP_TOC.unpack(f.read(NUP_TOC.size))
            if toc_magic != b"TOC\x01" or not 0 < count <= NUP_MAX_PACKAGES \
                    or NUP_TOC_SIZE + count * NUP_PACKAGE_SIZE > toc_size:
                return None
            f.seek(toc_offset + NUP_TOC_SIZE)
            table = f.read(count * NUP_PACKAGE_SIZE)
    except (OSError, struct.error):
        return None
    packages = [NUP_PACKAGE.unpack_from(table, i * NUP_PACKAGE_SIZE) for i in range(len(table) // NUP_PACKAGE_SIZE)]
    for kind in NUP_KINDS:
        for tid, *_, package_kind in packages:
            if package_kind == kind and is_title_id(f"{tid:016X}"):
                return base_id(f"{tid:016X}")
    return None


def read_app(path):
    """A Game app's facts from its Info.plist, or None for anything that isn't one."""
    try:
        with open(path / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    tid = info.get("DMTitleID")
    if not is_title_id(tid):
        return None
    text = lambda key: info[key].strip() if isinstance(info.get(key), str) and info[key].strip() else None
    return GameApp(path=path, title_id=tid.upper(), name=text("CFBundleDisplayName") or text("CFBundleName"),
                   commit=text("DMCommit"), chain=text("DMChain"),
                   bundle_id=text("CFBundleIdentifier"))


def _apps_in(folder):
    try:
        candidates = sorted(p for p in folder.iterdir() if p.suffix == ".app" and p.is_dir())
    except OSError:
        return []
    return [a for a in map(read_app, candidates) if a]


def _build_folder(folder, tid):
    path = folder / f"{tid}_build" if tid else None
    return path if path and path.is_dir() else None


def _game_folder(path):
    entries = list(path.iterdir())
    names = [p.name for p in entries]
    build = next((p for p in entries if p.is_dir() and BUILD_FOLDER.match(p.name)), None)
    tid = (BUILD_FOLDER.match(build.name).group(1).upper() if build else None) or title_id_from_names(names) \
        or next(filter(None, (nup_title_id(p) for p in entries if p.suffix.lower() == ".nup" and p.is_file())), None)
    apps = _apps_in(path)
    app = next((a for a in apps if a.title_id == tid), None) or (apps[0] if apps and not tid else None)
    tid = tid or (app.title_id if app else None)
    return Game(id=path.name, path=path, title_id=tid, app=app, build_folder=build)


def scan(library):
    """The Library folder's Games, in the order of their folders: sort them by sort_key() of
    their names once titledb's are known."""
    library = Path(library)
    try:
        entries = sorted(p for p in library.iterdir() if not p.name.startswith("."))
    except OSError:
        return []
    top_apps = None
    games = []
    for p in entries:
        if p.is_dir() and not _built_beside_nup(p.name):
            try:
                games.append(_game_folder(p))
            except OSError:
                continue
        elif p.is_file() and p.suffix.lower() == ".nup":
            if top_apps is None:
                top_apps = _apps_in(library)
            tid = title_id_from_names([p.name]) or nup_title_id(p)
            app = next((a for a in top_apps if a.title_id == tid), None) if tid else None
            games.append(Game(id=p.name, path=p, title_id=tid, app=app,
                              build_folder=_build_folder(library, tid)))
    return games


def _built_beside_nup(name):
    """A .nup's Game app, half-written Game app, build folder and, for a game nxnative has no
    profile for, its `<title ID>_survey` folder go in the Library folder itself; none of them is
    a Game folder. (The survey folder isn't in docs/frontends.md: it's read under ADR 0001's
    exception, where a wrong answer could only change what's shown.)"""
    return name.endswith((".app", ".app.partial")) or bool(BUILD_FOLDER.match(name) or SURVEY_FOLDER.match(name))


def sort_key(name):
    """Alphabetical, ignoring a leading "The", as Music and TV sort titles."""
    return LEADING_ARTICLE.sub("", name).casefold()
