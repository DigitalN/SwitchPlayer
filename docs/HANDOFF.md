# NX Player: handoff (2026-11-06)

For the next agent. Read [DESIGN.md](DESIGN.md), [GLOSSARY.md](../GLOSSARY.md) and [ADR 0001](adr/0001-only-the-documented-contract.md) first: they hold every agreed decision. This file says what's built, what isn't, and what to watch out for.

Nothing is committed yet (the repo has no commits). The maintainer's rule from Mac Engine Room (`~/Documents/GitHub/pythonflow/CLAUDE.md`) applies here too: work on a branch, not `main`; when told to commit, also push and open the PR.

## Done and approved

**The library grid** (`Application Files/index.html`), approved by the maintainer. Vanilla JS, pywebview bridge, no network, CSP like Mac Engine Room's. Polls `api.library()` once a second while visible; writes go through `put()` (skips unchanged values).
- Tiles: square art (play button on hover when a Game app exists), name (up to 2 lines), one-line status, Build/Stop button, detail line, progress bar (white on a dark inset track; accent blue vanished on blue art).
- Statuses: Not built · Waiting · "Building, step k of n" with detail "Stage name · 42% · 4 min left" (stage's own % and eta; bar = whole Build) · Stopped · Build failed (red, then nxnative's `[RED]` message clamped to 3 lines, then Show Build Log) · Ready (+ yellow "Your version is out of date, consider rebuilding" with a Build button, or grey "N newer nxnative changes").
- ⋯ menu (also right-click): Settings…, Build Options… | Show Log, Show in Finder | Rebuild, Import NX Play Save… (only with an NX Play save). Greyed when not applicable.
- Build Options… dialog: one text field, passed to the script as typed.
- Empty state: "No games in <folder>".

**Names come from the game itself** (maintainer's request; recorded in DESIGN.md): Game app's `CFBundleDisplayName` → titledb's name without ™®© → Game folder / `.nup` name only while neither is known. Sorted alphabetically ignoring a leading "The".

**Page API the page expects** (Python side must provide; see `tools/preview/stub.js` for exact shapes): `library()` → `{folder_name, games:[{id, name, title_id, art (a key, changes when art changes), state: none|waiting|building|stopped|failed|ready, app (bool), progress {k,n,name,step,frac,eta}|null, error, out_of_date, newer, log (bool), nxplay_save, options}]}`, `art(id)` → data: URL, `play`, `open_settings`, `build`, `stop`, `show_log`, `show_build_log`, `show_in_finder`, `import_nxplay_save`, `set_build_options(id, text)`, `log_error(msg)`.

## Written and tested, not yet wired into an app

All in `Application Files/`. `python3 -m pytest` → 38 tests pass (`tests/`).

- `titledb.py`: names and art from blawar/titledb `US.en.json` (adapted from NX Play's Artwork.swift; MIT notice in `THIRD-PARTY-NOTICES`). Streams the 90 MB list one entry at a time (53 MB peak vs 650 MB parsed whole), keeps only the library's titles in `index.json`, lowest nsuId wins (SMP Jamboree has a later Switch 2 Edition listing), images only from `https://img-eshop.cdn.nintendo.net`, checked to be JPEG, kept in `art/<title ID>.jpg`. List fetched at most once a launch; titles it lacks are retried after a week. Downloads via `/usr/bin/curl` (system certs).
- `library.py`: scans the Library folder (subfolders, or top-level `.nup`; ignores loose `.nsp`, `.app`, `.app.partial`, `<tid>_build` at top level). Title ID from `<tid>_build` folder, `[tid]` tags (update/DLC → base), or the Game app's `DMTitleID`. Reads Game apps' Info.plist (`CFBundleDisplayName`, `DMTitleID`, `DMCommit`, `DMChain`). `Game.name(titledb_name)`, `sort_key()`, `nxplay_save` (`<tid>_build/run/save` exists).
- `builds.py`: the Build queue/runner. One at a time; `build`, `stop` (waiting → out of queue; building → SIGINT to its process group, started with `start_new_session`), `stop_all` (Quit: SIGINT, wait 20 s, then SIGKILL), `status`, `busy`, `build_log`. Parses `--progress json` events; exit 0 done (job dropped, Game app speaks for itself), 130 stopped, 1 failed with last `[RED]` message or stderr's last line, 2 "didn't accept the Build Options: …". Writes a readable Build log per game (`<log dir>/<game id>.log`, previous as `.prev.log`). Takes `prepare(job)` → (argv, cwd, env) run on the Build's thread, `on_finish(job, ok)`, `on_busy(busy)`. Tested with `tests/fake_build.py`, a stand-in script with the same events, exit codes and SIGINT behaviour.

## Written, not yet tested

- `settings.py`: JSON settings (`library_folder`, `keys_file`, `nxnative_checkout`, `build_options` per game id), validated; first launch copies NX Play's `gamesRoot` and `keysPath` from `com.varyform.nxplay` (read only). Needs `tests/test_settings.py`.
- `nxnative.py`: `Copy` (own blobless clone of `https://github.com/seedds/nxnative.git` into the data folder via a `.partial` folder; `update()` = fetch + `reset --hard origin/HEAD`, own copy only; a player's checkout is never changed), `Environment` (uv-managed Python + packages from `nxnative-python/pyproject.toml`, NX Play's list, in the data folder; `synced-pyproject.toml` marks a finished sync), `build_command()` (argv `python3 scripts/build_ipa_from_nsp.py <folder> --platform mac-app --progress json [-k keys] [options]`, cwd = the copy, PATH = env/bin + `/opt/homebrew/bin` `/usr/local/bin` `/usr/local/share/dotnet` + system; drops PYTHON*/DYLD_*/_PYI* vars). Needs tests: git against a local bare repo (clone, update following a force push), command line, env.

## Next steps, in order

1. **Tests for `settings.py` and `nxnative.py`.** Then update `THIRD-PARTY-NOTICES` to also credit NX Play for `nxnative-python/pyproject.toml`.
2. **`main.py`**, modelled on Mac Engine Room's (`~/Documents/GitHub/pythonflow/Application Files/main.py`): logging (`~/Library/Logs/NX Player/app.log`), single-instance lock, `NXPLAYER_DATA_DIR` override (data and logs there), pywebview window (`html=` index.html, `js_api=Api`, `private_mode=True`), `Api` with the methods above (keep internals behind `_`; AppKit calls via `AppHelper.callAfter`). Regular Dock app (unlike Mac Engine Room): closing the window hides it and Builds go on; Dock click reopens it (`applicationShouldHandleReopen`); Quit asks "Stop the Build and quit?" when `builder.busy` (`applicationShouldTerminate_`), then `stop_all`. Scan the library at most every ~2 s and after each Build; run `titledb.update` on a background thread for games lacking a name or art; art fallback after a Build = the Game app's `Contents/Resources/AppIcon.icns` converted with `sips` (cached). `prepare(job)` = `Copy.update()` + `Environment.ensure()` + `build_command()`. Play = `open <app> --args --no-launcher`; Settings… = `open <app>`; Show Log = nxnative's `~/Library/Logs/nxnative/<tid>.log`; Show in Finder = reveal the Game app, else the Game folder. Idle sleep while busy: `/usr/bin/caffeinate -i -w <own pid>`, killed when idle. Notifications when a Build finishes or fails. A way to choose the Library folder and keys file (no UI was designed: a native menu via pywebview's `menu=` or a button in the empty state; add a "no folder chosen" state to the page and stub).
3. **Moving off NX Play** (DESIGN.md): at a game's first Build, if `~/Library/Application Support/nxnative/<tid>/save` doesn't exist, copy `<tid>_build/run/save` there (leave the original), and write NX Play's global settings (`DM_FULLSCREEN=1`, `DM_PRESENT_UPSCALE=metalfx`) as `launcher.<VAR>` strings in `org.nxnative.<tid lower>` only where they differ from `launcher.json`'s defaults and the key isn't already set (Echoes of Wisdom already has `launcher.DM_PRESENT_UPSCALE`). Do the save copy before the Build starts. Skip all of this when `NXPLAYER_DATA_DIR` is set, so test runs never touch the real folders. Then Import NX Play Save… (dates of both, ask, back up the current save to `save-backups/<date> before NX Play import`, refuse while the game runs).
4. **Out of date and the grey count** (DESIGN.md "Playing", PR 4 fallback): compare `DMCommit` with the copy, commits touching `dm/games/<tid>/`, `DMSH_CHAIN` in `dm/gfx/nvn/dmsh_chain.h` vs `DMChain`; Game folder gained files since. Fetch at open, daily after wake, Check Now.
5. **Prerequisites at first launch** (uv env, Homebrew packages with a button: `uv`, `xcodegen`, `zstd`, `glslang`, cask `dotnet-sdk`; Xcode manual), from NX Play's `bin/setup` checks.
6. **Packaging and updates**: PyInstaller spec + `scripts/build.sh` (build in `~/Library/Caches`, `xattr -cr`, sign with team `XT4LT7MS6Y`), bundle id `com.digitaln.nxplayer`, Mac Engine Room's updater against `DigitalN/NXPlayer` releases, with its busy hook = `builder.busy`.
7. The four nxnative pull requests in DESIGN.md (each has a fallback, so they're independent).
8. A `CLAUDE.md` for this repo once `main.py` exists (architecture, debugging, testing rules), like Mac Engine Room's.

## Testing rules

- **The page:** `page-preview` in `.claude/launch.json` runs `tools/preview/serve.py` on port 8766 with `stub.js` standing in for the API. Real games from `~/Downloads/Switch Games` (read only), named and with art by the app's own `library.py`/`titledb.py`; downloads kept in `tools/preview/.data/` (gitignored). States: `?s=building|ready|failed|noart|many|empty` plus `dark|light`, combinable. Build/Stop/queue are simulated. When the page gains a state or API call, add it to the stub. Check light and dark and narrow widths before showing the maintainer.
- **Builds:** a test Build must run on a copy of a Game folder (`cp -c` clones on APFS, so no extra space) with `NXPLAYER_DATA_DIR` set. nxnative keeps everything a Build's own game runs write inside `<tid>_build/tmp/`, so a Build doesn't touch real saves; **Play does** (`~/Library/Application Support/nxnative/<tid>/`, `org.nxnative.<tid>` prefs, `~/Library/Logs/nxnative/`). Say so before anything opens on screen, and ask before a real (long, CPU-heavy) Build.
- Don't touch `~/nxnative`, `~/dev/nxnative*`, `~/Documents/GitHub/nxnative`, NX Play or its build folders' `run/save` beyond reading.
- The maintainer's Mac: Xcode 27.2 selected, Homebrew with uv, xcodegen, zstd, glslang, dotnet all present; pywebview 6.1 and PyObjC 12.1 in the framework Python 3.13. None of the three games has a Game app yet (NX Play built them with `--platform mac`), and all three have NX Play saves: Echoes of Wisdom `01008CF01BAAC000`, Rhythm Heaven `0100D9F01D474000`, SMP Jamboree `0100965017338000`.

## Files

```
Application Files/index.html            the page (approved)
Application Files/titledb.py            names and art (tested)
Application Files/library.py            Library scan, names, sorting (tested)
Application Files/builds.py             Build queue and runner (tested)
Application Files/settings.py           NX Player's settings (untested)
Application Files/nxnative.py           nxnative copy, Python env, command line (untested)
Application Files/nxnative-python/pyproject.toml   the build script's Python packages
tests/                                  conftest, test_titledb, test_library, test_builds, fake_build.py
tools/preview/serve.py, stub.js         the page preview
.claude/launch.json                     page-preview on port 8766
THIRD-PARTY-NOTICES, .gitignore
docs/DESIGN.md, docs/adr/0001-…, GLOSSARY.md, docs/HANDOFF.md
```
