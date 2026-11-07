# Switch Player design

Agreed with the maintainer on 2026-11-03, before any code. Terms are in [GLOSSARY.md](../GLOSSARY.md); the rule every choice here follows is [ADR 0001](adr/0001-only-the-documented-contract.md): nxnative stays the easiest thing to update, and Switch Player relies only on what nxnative documents for front ends ([nxnative's docs/frontends.md](https://github.com/seedds/nxnative/blob/main/docs/frontends.md)). When a request competes with that, discuss it before building it.

## What it is

A Mac app that builds and plays the maintainer's own Switch games through nxnative, with no Terminal. Python + pywebview + vanilla JS, packed with PyInstaller (the stack of Mac Engine Room, `~/Documents/GitHub/pythonflow`). Bundle identifier `com.digitaln.switchplayer` (never changes after the first release). Mac only: no IPAs. Apple silicon, macOS 26.6 or later, as nxnative needs.

## Library

- One Library folder, one Game folder per Game: a subfolder with the base game, update and DLC, or a single `.nup` file. Loose `.nsp` files at the top level are ignored.
- **Names** are the game's own, never just the Game folder's, which the player may not have named well: after a Build, its Game app's (`CFBundleDisplayName`, which nxnative takes from the game's files); before, titledb's, without ™, ® and ©; the Game folder's (or `.nup`'s) name only while neither is known. Up to two lines, sorted alphabetically, ignoring a leading "The".
- A grid of square album art. Under each: the name and a one-line status (Not built, Built in NX Play, Waiting, Preparing Build with what it's doing (Updating nxnative, or Installing Python packages only while they install), Building with stage, % and time left, Stopped, Build failed with nxnative's `[RED]` message and a Show Build Log link, Ready). While building, a progress bar across the bottom of the art.
- **Built in NX Play**: NX Play built the game with nxnative's developer path (`--platform mac`), which makes no Game app (ADR 0001 rules that path out for Switch Player). Told apart by the files only NX Play writes into the build folder, `start_mac.sh` or `nxplay-settings.json`: the same `<title ID>_build` folder is left by a Switch Player Build that stopped or failed, which shows Not built. Detail: "Build once to play it here. It reuses NX Play's files and brings your save." The Build reuses the build folder's extracted files and shader cache.
- Clicking the art plays. A small **Build** or **Stop** button sits beside the status when there's something to build or stop.
- **⋯**: Settings…, Build Options…, Show Log, Show in Finder, Rebuild, Import NX Play Save… (only for a game with an NX Play save).
- **Art**: the eShop's 1024×1024 icon (the game's own icon is only 256×256, soft at grid sizes). Found through blawar/titledb's `US.en.json` (26 MB gzipped, fetched only when a game without art or a name appears, and read one entry at a time; keep only the library's entries; a title listed twice takes its lowest nsuId's entry, the original listing), images from Nintendo's CDN only, kept for good. Before the first Build when the title ID can be told from file names or an existing build folder. Offline or unlisted: the Game app's own icon after its first Build, a plain tile with the name before.

## Building

- Only when the player presses Build. One at a time; others wait in a queue. Stop works at any point, and takes a waiting one out of the queue.
- The Game app goes in its Game folder (nxnative's default; its big files are APFS clones there). Build folders (`<title ID>_build`, 3–19 GB) stay, so rebuilds take minutes.
- Idle sleep is held off while a Build runs or waits; the display may sleep.
- A notification when a Build finishes or fails.
- Closing the window keeps building (Switch Player stays in the Dock); Quit asks "Stop the Build and quit?".
- **Build options**: read from the build script once nxnative can list them for a program (pull request below); until then a text field per game, passed to the script as typed. Nothing set by default.

## Playing

- Play starts the Game app straight into the game with its saved Game settings (skips its Settings window).
- **Settings…** opens the Game app with its own Settings window: Play there saves the choices, Quit throws them away. Switch Player never draws Game settings.
- **Out of date** (yellow: "Your version is out of date, consider rebuilding"): nxnative changed something for this game specifically (its own files in nxnative, or the shader version) since the Game app was built, or the Game folder gained files since. Other newer nxnative changes show only as a grey count ("142 newer nxnative changes"). Play stays available either way.

## nxnative

- Switch Player keeps its own clone of `seedds/nxnative` in its Application Support folder. It never touches `~/nxnative` or the clones in `~/dev`. A setting can point it at another checkout, which Switch Player then never changes.
- Engine update: it fetches when it opens, once a day after a wake, and on **Check for nxnative Updates…** (for the warnings), and moves its copy to upstream's latest only when a Build starts. Its own clone has no local commits, so it can always follow upstream, force pushes included.
- Builds use whichever Xcode macOS has selected. Switch Player never names an Xcode version; nxnative's own error says when another is needed, and Switch Player shows it.
- **Check for nxnative Updates…** answers the way Check for Updates… does: it compares in the background, then a native alert says either "No games built in Switch Player yet", "Your games are up to date with nxnative", or "nxnative has changes for N games" naming the Out of date ones and counting the rest, or why it couldn't check. The automatic checks only update the tiles.
- At every launch, a dismissible check reports every prerequisite; Build Setup… reopens it. Nothing installs until the player asks: Homebrew packages install with a button. Xcode and its Metal Toolchain stay manual. The check never mentions Switch Player's own Python environment (uv): a Build prepares it when it's missing or its package list changed, and the tile says so.

## App updates

Mac Engine Room's updater (`Application Files/updater.py` and the helpers it imports from `launch.py`; *How updates work* and *Release checklist* in `docs/DEVELOPMENT.md`): GitHub releases of the public `DigitalN/SwitchPlayer` repo, a `.dmg` signed with the maintainer's Apple Development certificate (team `XT4LT7MS6Y`), checked once a day after a wake, plus Check for Updates… in the application menu. Its busy hook holds the relaunch while a Build runs or waits.

## Moving off NX Play

- First launch fills in NX Play's Library folder and keys file (below).
- At each game's first Build, if the Game app has no save yet: copy NX Play's save (`<title ID>_build/run/save`) to the Game app's (`~/Library/Application Support/nxnative/<title ID>/save`), leaving the original. Game settings are not migrated.
- Import NX Play Save… does the copy on demand: shows both saves' dates, asks, first moves the current save to `save-backups/<date> before NX Play import` beside it, and refuses while the game runs. Every step is a native alert on the main thread, ending with "NX Play save imported" and where the backup went.
- The maintainer deletes NX Play once each game has been played in Switch Player.

## Pull requests to nxnative

Switch Player works without each; each removes something Switch Player would otherwise keep in step with nxnative.

1. A switch that shows a Game app's Settings window for one start even when its "Show this window at launch" is off (`Launcher.swift` checks only the `window.show` default and the Option key). Until then, Settings… needs that window left on.
2. The build script's options in a form a program can read (e.g. `--options json`). Until then, the text field.
3. nxnative's dependencies as files (Python version, Python packages, Homebrew packages), installed from the nxnative copy after every Engine update. Until then, NX Play's list.
4. `docs/frontends.md` documents what tells a Game app is out of date: `DMCommit` in its Info.plist, `dm/games/<title ID>/`, and the shader version (`DMSH_CHAIN` in `dm/gfx/nvn/dmsh_chain.h`, `DMChain` in the app). Until then Switch Player reads them under ADR 0001's exception, and shows only the grey count when any is missing.

## Facts the build depends on

From nxnative at `8c80f39` (2026-11-03).

**Building.** `python3 scripts/build_ipa_from_nsp.py <Game folder or .nup> --platform mac-app --progress json [-k <prod.keys>]`, run from the nxnative copy with Switch Player's Python environment first on `PATH` (nxnative's shell tools call a bare `python3`). A GUI app doesn't get the shell's `PATH`: add `/opt/homebrew/bin` and `/usr/local/share/dotnet`. Keys aren't needed for a `.nup` or a dump folder.
- stdout: one JSON object per line, each with `ev` and `t`: `stage` (`k`, `n`, `name`, `step`; `n` can grow), `step`, `progress` (`done`, `total`, `unit`, `frac` 0–0.99 or null, `eta`, `elapsed`, `note`), `stage_end` (`ok`, `summary`, `elapsed`), `shaders_stale`, `say` (`msg`; starting `[RED] ` is an error).
- Exit 0 done, 1 error (the last `say` says why; a traceback on stderr if unexpected), 2 bad arguments (usage on stderr, no JSON), 130 interrupted. Capture stderr too.
- Tools' full output: `<Game folder>/<title ID>_build/logs/`.
- Stop: SIGINT to the build's process group (start it in its own session) → exit 130. Steps write `.partial` folders renamed when done, and the app is written as `<app>.partial`, so the last Game app stays playable and the next Build reuses finished steps.

**A Game app.** `<game name>.app` in the Game folder (a `:` or `/` in the name becomes ` - `). Info.plist: `CFBundleDisplayName` (the game's name, `:` and all), `DMTitleID`, `DMCommit` (12-character short hash of the nxnative checkout, `+dirty` with uncommitted changes), `DMChain`, `DMSettings`. Settings list: `Contents/Resources/dm/launcher.json` (schema in nxnative's `docs/launcher.schema.json`). Icon: `Contents/Resources/AppIcon.icns`, made from the game's 256×256 `icon.jpg`.
- Start without the Settings window: `open <app> --args --no-launcher` (or `DM_LAUNCHER=0` in its environment). Saved choices still apply.
- Saved choices: preferences domain `org.nxnative.<title ID in lower case>` (its `CFBundleIdentifier`), keys `launcher.<VAR>` (strings), only for values that differ from the tested default; the launcher ignores values the build doesn't offer. The window's own switch is `window.show`.
- Save `~/Library/Application Support/nxnative/<title ID>/save/`; backups in `save-backups/` beside it (nxnative prunes only its own `local` / `cloud` folders and leaves others alone). Log `~/Library/Logs/nxnative/<title ID>.log`, previous run `<title ID>.prev.log`.

**Prerequisites.** Xcode 27.2 today (nxnative pins its Metal compiler), its Metal toolchain (`xcodebuild -downloadComponent MetalToolchain`), Homebrew with `xcodegen`, `zstd`, `glslang` and the `dotnet-sdk` cask, an arm64 Python 3.12+ with `lz4`, `capstone>=5,<6`, `fonttools`, `nsz`, `zbic` and `backports.zstd` before 3.14. On the maintainer's M1 Pro, all are present except those Python packages (the framework Python 3.13 has only `fonttools`).

**From NX Play** ([varyform/nx-native-wrapper](https://github.com/varyform/nx-native-wrapper), MIT, © 2026 Oleh Khomei: keep its notice with anything taken):
- `pyproject.toml` (the Python packages, with uv) and `Brewfile`.
- `bin/setup`: the prerequisite checks (Xcode first launch, Metal toolchain, `brew bundle check`, .NET).
- `nxplay`'s `ask_update()` and `no_local_commits()`: the commit a build was made from, commits since, those touching `dm/games/<id>`, and whether the shaders will be recompiled (`DMSH_CHAIN` differs) or checked again (a diff under `dm/gfx/tools dm/ios/tools dm/games/game.py dm/tools/romfs.py dm/tools/nso.py dm/games/<id>`).
- `Packages/NXPlayKit/Sources/NXPlayKit/Model/Artwork.swift`: the titledb lookup and Nintendo-CDN-only image download.
- Not its settings screen or its `--platform mac` flow (ADR 0001).

**The maintainer's NX Play setup** (preferences domain `com.varyform.nxplay`): `gamesRoot` `~/Downloads/Switch Games`, `keysPath` `~/Downloads/Switch Games/prod.keys`, `globalSettings` `DM_FULLSCREEN=1` and `DM_PRESENT_UPSCALE=metalfx`. Games with saves in `run/save`: Echoes of Wisdom (`01008CF01BAAC000`), Rhythm Heaven (`0100D9F01D474000`), SMP Jamboree (`0100965017338000`). Echoes of Wisdom already has `launcher.DM_PRESENT_UPSCALE` in its nxnative domain.

## Before building on the maintainer's Mac

- Saves, saved choices and logs live under nxnative's own folders and preferences, not Switch Player's, so a test Build, Play or import touches the real ones. Test against a copy of a Game folder and with a data-folder override, as Mac Engine Room does with `PYTHONFLOW_DATA_DIR`, and say so before anything opens on screen.
- Don't touch `~/nxnative`, the `~/dev/nxnative*` clones, NX Play or its build folders' `run/save` beyond reading.
- Check the grid in a page preview with the three games before building more on it.
