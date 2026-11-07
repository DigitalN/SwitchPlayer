# Switch Player relies only on nxnative's documented front-end contract

Switch Player's first priority is that nxnative stays the easiest thing to update: its changes reach the player without waiting for an Switch Player release. Anything that competes with that is discussed before it's built.

So Switch Player uses only what nxnative documents for front ends (`docs/frontends.md`): the build script's command line, its `--progress json` events and exit codes, and each Game app's `Info.plist`, `launcher.json`, saved choices and `env.txt` files. Anything more it needs goes to nxnative as a pull request, never as a workaround here. Every nxnative internal Switch Player relied on would bring back the wait for an Switch Player release, as it did for NX Play.

One exception, and only until that pull request is merged, whether or not it has been opened yet: Switch Player may read an undocumented nxnative detail where a failure could only change what Switch Player shows, never a Build or Play. (Amended 2026-11-07: this said "only while that pull request is open", which nothing met while the pull request was still to be written.)

A second exception, on the same terms: for a settings list the Settings panel can't draw (a newer schema), **Open the Game's Settings Window** removes the Game app's saved "Show this window at launch" (`window.show`) just before it opens the app, so the window shows. That writes an undocumented key, which the first exception doesn't allow, but only that one key and only from that button. A failure leaves the button starting the game, and can't change a Build or Play, which always skips the window. It ends when the panel can draw every list nxnative writes, or when nxnative documents a way to show the window. (Added 2026-11-07, for Settings… itself; narrowed the same day to this fallback, once the Settings panel replaced the window.)

## Considered options

- **Draw the Game settings in Switch Player** from each Game app's `launcher.json`. Rejected at first (2026-11-03): it duplicates the Game app's own Settings window, which nxnative keeps up to date. **Taken 2026-11-07**, as the Settings panel: the window's Play and Quit read as starting the game when the player only meant to change a setting, it was tall, and Settings… couldn't open it once the player turned it off. It stays within this ADR: `docs/frontends.md` documents the settings list (with a JSON Schema and its compatibility rules: schema 1 grows only by optional fields, a new kind is schema 2), the saved choices a front end writes, the order of the `env.txt` files, and `DM_LAUNCHER=0` for a front end that draws the settings itself. Drawn from the same `launcher.json` as the window, a new setting reaches the panel with the Build that brings it; only a schema 2 list needs a Switch Player release, and until then the panel opens the game's own window (the second exception).
- **Build with nxnative's developer path** (`--platform mac`) and rerun the command it prints, as NX Play does. Rejected: that path is for nxnative's developers, not front ends.
- **Keep nxnative's dependency list, option list or Xcode version in Switch Player.** Rejected: each would need an Switch Player release whenever nxnative changed it.

## Consequences

- Switch Player doesn't read game logs while a game runs: nxnative's log lines are for people, and the doc says not to treat them as an interface.
- Switch Player builds with whichever Xcode macOS has selected and never names a version. When nxnative needs another, its own error says which, and Switch Player shows that error.
- Pull requests to nxnative, each with a fallback so Switch Player works without it (four at first; the first was dropped on 2026-11-07):
  - ~~**A switch that shows a Game app's Settings window** even when the player turned it off.~~ Dropped: Switch Player draws the Game settings itself, and opens the game's own window only for a list it can't draw (the second exception above).
  - **The build script's options in a form a program can read.** Until then, **Build Options…** is a text field passed to the script as typed.
  - **nxnative's dependencies as files** (Python version, Python packages, Homebrew packages), which Switch Player installs from after every Engine update. Until then, Switch Player uses NX Play's list.
  - **Documenting what tells a front end a Game app is Out of date**: the commit a Game app records, the folder holding each game's own files, the shader version, and the folders nxnative writes beside a game. Until then, Switch Player reads them under the exception above, and if any of them is missing it never shows Out of date, only that nxnative has changes since the last Build. (Amended 2026-11-07: this said it showed a count of newer changes, which the tiles no longer show.)
