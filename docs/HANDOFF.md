# Switch Player: handoff (2026-11-06)

For the next agent. Read [DESIGN.md](DESIGN.md), [GLOSSARY.md](../GLOSSARY.md) and [ADR 0001](adr/0001-only-the-documented-contract.md) first: they hold every agreed decision. This file says what's built, what isn't, and what to watch out for.

The maintainer's rule from Mac Engine Room (`~/Documents/GitHub/pythonflow/CLAUDE.md`) applies here too: work on a branch, not `main`; when told to commit, also push and open the PR.

## Done and approved

**The library grid** (`Application Files/index.html`), approved by the maintainer. Vanilla JS, pywebview bridge, no network, CSP like Mac Engine Room's. Polls `api.library()` once a second while visible; writes go through `put()` (skips unchanged values).
- Tiles: square art (play button on hover when a Game app exists), name (up to 2 lines), one-line status, Build/Stop button, detail line, progress bar (white on a dark inset track; accent blue vanished on blue art).
- Statuses: Not built · Waiting · "Building, step k of n" with detail "Stage name · 42% · 4 min left" (stage's own % and eta; bar = whole Build) · Stopped · Build failed (red, then nxnative's `[RED]` message clamped to 3 lines, then Show Build Log) · Ready (+ yellow "Your version is out of date, consider rebuilding" with a Build button, or grey "N newer nxnative changes").
- ⋯ menu (also right-click): Settings…, Build Options… | Show Log, Show in Finder | Rebuild, Import NX Play Save… (only with an NX Play save). Greyed when not applicable.
- Build Options… dialog: one text field, passed to the script as typed.
- Empty states: "No games in <folder>", or a Choose Library Folder… button when no Library has been chosen. The preview has a `nofolder` scenario.

**Names come from the game itself** (maintainer's request; recorded in DESIGN.md): Game app's `CFBundleDisplayName` → titledb's name without ™®© → Game folder / `.nup` name only while neither is known. Sorted alphabetically ignoring a leading "The".

**Page API the page expects** (Python side must provide; see `tools/preview/stub.js` for exact shapes): `library()` → `{folder_name, games:[{id, name, title_id, art (a key, changes when art changes), state: none|waiting|building|stopped|failed|ready, app (bool), progress {k,n,name,step,frac,eta}|null, error, out_of_date, newer, log (bool), nxplay_save, options}]}`, `art(id)` → data: URL, `choose_library`, `play`, `open_settings`, `build`, `stop`, `show_log`, `show_build_log`, `show_in_finder`, `import_nxplay_save`, `set_build_options(id, text)`, `log_error(msg)`.

## Written and tested

All in `Application Files/`. `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest` → 92 tests pass (`tests/`).

- `main.py`: the pywebview app and public page API, wired to the modules below. Includes native Play/Settings/Finder/log actions, Build preparation, two-second scan throttling and forced post-Build scans, background titledb updates, cached app-icon conversion, Build notifications and sleep prevention, a regular Dock window whose close action hides it, busy-Quit confirmation, application-menu Library/keys pickers, logging and the single-instance lock. `SWITCHPLAYER_DATA_DIR` isolates app data, logs and save migration. `tests/test_main.py` exercises the public API and native lifecycle with simulated services and a fake nxnative script; the real native app has not been opened in this step. Freshness fetches in the background at the first Library scan, at most daily after wake, and from Check for nxnative Changes Now.
- `migration.py`: save-game migration only, not Game settings. Before a Build, copies an NX Play save to the Game app's save folder only if none exists, leaving the original untouched. Import NX Play Save… shows both save dates, asks with Import/Cancel, refuses while the game runs, and backs up the current save before replacing it. Missing sources, copy failures and backup-name collisions leave the current save intact. `tests/test_migration.py` and the public-API tests use temporary save folders only.
- `titledb.py`: names and art from blawar/titledb `US.en.json` (adapted from NX Play's Artwork.swift; MIT notice in `THIRD-PARTY-NOTICES`). Streams the 90 MB list one entry at a time (53 MB peak vs 650 MB parsed whole), keeps only the library's titles in `index.json`, lowest nsuId wins (SMP Jamboree has a later Switch 2 Edition listing), images only from `https://img-eshop.cdn.nintendo.net`, checked to be JPEG, kept in `art/<title ID>.jpg`. List fetched at most once a launch; titles it lacks are retried after a week. Downloads via `/usr/bin/curl` (system certs).
- `library.py`: scans the Library folder (subfolders, or top-level `.nup`; ignores loose `.nsp`, `.app`, `.app.partial`, `<tid>_build` at top level). Title ID from `<tid>_build` folder, `[tid]` tags (update/DLC → base), or the Game app's `DMTitleID`. Reads Game apps' Info.plist (`CFBundleDisplayName`, `DMTitleID`, `DMCommit`, `DMChain`). `Game.name(titledb_name)`, `sort_key()`, `nxplay_save` (`<tid>_build/run/save` exists).
- `builds.py`: the Build queue/runner. One at a time; `build`, `stop` (waiting → out of queue; building → SIGINT to its process group, started with `start_new_session`), `stop_all` (Quit: SIGINT, wait 20 s, then SIGKILL), `status`, `busy`, `build_log`. Parses `--progress json` events; exit 0 done (job dropped, Game app speaks for itself), 130 stopped, 1 failed with last `[RED]` message or stderr's last line, 2 "didn't accept the Build Options: …". Writes a readable Build log per game (`<log dir>/<game id>.log`, previous as `.prev.log`). Takes `prepare(job)` → (argv, cwd, env) run on the Build's thread, `on_finish(job, ok)`, `on_busy(busy)`. Tested with `tests/fake_build.py`, a stand-in script with the same events, exit codes and SIGINT behaviour.
- `settings.py`: JSON settings (`library_folder`, `keys_file`, `nxnative_checkout`, `build_options` per game id), validated; first launch copies NX Play's `gamesRoot` and `keysPath` from `com.varyform.nxplay` (read only).
- `nxnative.py`: `Copy` (own blobless clone of `https://github.com/seedds/nxnative.git` into the data folder via a `.partial` folder; `update()` = fetch + `reset --hard origin/HEAD`, own copy only; a player's checkout is never changed), `Environment` (uv-managed Python + packages from `nxnative-python/pyproject.toml`, NX Play's list, in the data folder; `synced-pyproject.toml` marks a finished sync), `build_command()` (argv `python3 scripts/build_ipa_from_nsp.py <folder> --platform mac-app --progress json [-k keys] [options]`, cwd = the copy, PATH = env/bin + `/opt/homebrew/bin` `/usr/local/bin` `/usr/local/share/dotnet` + system; drops PYTHON*/DYLD_*/_PYI* vars). `freshness()` compares each Game app's `DMCommit` and `DMChain` with upstream without moving the checkout: game-specific or shader-chain changes are Out of date; other commits supply the grey count. New Game files and dirty Builds also warn. Missing metadata falls back to no warning/count. Git tests use local bare repos, including an upstream force push and a player checkout that is never fetched.

## Next steps, in order

1. **Moving off NX Play** (DESIGN.md): save-game migration is implemented. At a game's first Build, if `~/Library/Application Support/nxnative/<tid>/save` doesn't exist, copy `<tid>_build/run/save` there and leave the original. Import NX Play Save… shows both dates, asks with Import/Cancel, backs up the current save to `save-backups/<date> before NX Play import`, and refuses while the game runs. Game settings are deliberately not migrated. Tests inject an isolated save root; `SWITCHPLAYER_DATA_DIR` also redirects save migration for test runs.
2. **Out of date and the grey count**: implemented as described above. The existing Ready tile and `ready` preview scenario display the warning and count; no page changes were needed.
3. **Prerequisites at first launch** (uv env, Homebrew packages with a button: `uv`, `xcodegen`, `zstd`, `glslang`, cask `dotnet-sdk`; Xcode manual), from NX Play's `bin/setup` checks.
4. **Packaging and updates**: PyInstaller spec + `scripts/build.sh` (build in `~/Library/Caches`, `xattr -cr`, sign with team `XT4LT7MS6Y`), bundle id `com.digitaln.switchplayer`, Mac Engine Room's updater against `DigitalN/SwitchPlayer` releases, with its busy hook = `builder.busy`.
5. The four nxnative pull requests in DESIGN.md (each has a fallback, so they're independent).
6. A `CLAUDE.md` for this repo once `main.py` exists (architecture, debugging, testing rules), like Mac Engine Room's.

## Testing rules

- **The page:** `page-preview` in `.claude/launch.json` runs `tools/preview/serve.py` on port 8766 with `stub.js` standing in for the API. Real games from `~/Downloads/Switch Games` (read only), named and with art by the app's own `library.py`/`titledb.py`; downloads kept in `tools/preview/.data/` (gitignored). States: `?s=building|ready|failed|noart|many|empty|nofolder` plus `dark|light`, combinable. Build/Stop/queue are simulated. When the page gains a state or API call, add it to the stub. Check light and dark and narrow widths before showing the maintainer.
- **Builds:** a test Build must run on a copy of a Game folder (`cp -c` clones on APFS, so no extra space) with `SWITCHPLAYER_DATA_DIR` set. nxnative keeps everything a Build's own game runs write inside `<tid>_build/tmp/`, so a Build doesn't touch real saves; **Play does** (`~/Library/Application Support/nxnative/<tid>/`, `org.nxnative.<tid>` prefs, `~/Library/Logs/nxnative/`). Say so before anything opens on screen, and ask before a real (long, CPU-heavy) Build.
- Don't touch `~/nxnative`, `~/dev/nxnative*`, `~/Documents/GitHub/nxnative`, NX Play or its build folders' `run/save` beyond reading.
- The maintainer's Mac: Xcode 27.2 selected, Homebrew with uv, xcodegen, zstd, glslang, dotnet all present; pywebview 6.1 and PyObjC 12.1 in the framework Python 3.13. None of the three games has a Game app yet (NX Play built them with `--platform mac`), and all three have NX Play saves: Echoes of Wisdom `01008CF01BAAC000`, Rhythm Heaven `0100D9F01D474000`, SMP Jamboree `0100965017338000`.

## Files

```
Application Files/index.html            the page (approved)
Application Files/main.py               app shell, public page API and native lifecycle
Application Files/migration.py          NX Play save migration and backups (tested)
Application Files/titledb.py            names and art (tested)
Application Files/library.py            Library scan, names, sorting (tested)
Application Files/builds.py             Build queue and runner (tested)
Application Files/settings.py           Switch Player's settings (tested)
Application Files/nxnative.py           nxnative copy, Python env, command line (tested)
Application Files/nxnative-python/pyproject.toml   the build script's Python packages
tests/                                  conftest, test_titledb, test_library, test_builds, test_main, test_settings, test_nxnative, fake_build.py
tools/preview/serve.py, stub.js         the page preview
.claude/launch.json                     page-preview on port 8766
THIRD-PARTY-NOTICES, .gitignore
docs/DESIGN.md, docs/adr/0001-…, GLOSSARY.md, docs/HANDOFF.md
```
