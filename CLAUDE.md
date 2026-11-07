# Switch Player

A Mac app (Python, pywebview, vanilla JS) that builds and plays the maintainer's own Switch games through nxnative.

- **Decisions**: [docs/DESIGN.md](docs/DESIGN.md) and [ADR 0001](docs/adr/0001-only-the-documented-contract.md). Raise anything that competes with them before building it.
- **Terms**: [GLOSSARY.md](GLOSSARY.md). Use its words in code, UI text and PRs.
- **Tests, app build, icon, release**: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Working

- Work on a branch. When the maintainer says commit, also push and open the PR.
- Page changes: check them in the `page-preview` server (`.claude/launch.json`) in light, dark and a narrow window. When the page gains a state or an API call, add it to `tools/preview/stub.js`.

## The maintainer's games and saves

- Play writes the player's real saves (`~/Library/Application Support/nxnative/<title ID>/`, `org.nxnative.<title ID>` prefs, `~/Library/Logs/nxnative/`). Ask before anything opens on screen, and before a real Build (long and CPU-heavy).
- A test Build runs on an APFS clone (`cp -c`) of a Game folder with `SWITCHPLAYER_DATA_DIR` set. nxnative keeps what a Build's own run writes inside `<title ID>_build/tmp/`.
- Read only: `~/nxnative`, `~/dev/nxnative*`, `~/Documents/GitHub/nxnative`, NX Play, and its build folders' `run/save`.
- The Library is `~/Switch Games` (Switch Player's settings); NX Play's `start_mac.sh` scripts there still point at the old `~/Downloads/Switch Games`.
