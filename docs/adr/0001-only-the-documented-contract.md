# Switch Player relies only on nxnative's documented front-end contract

Switch Player's first priority is that nxnative stays the easiest thing to update: its changes reach the player without waiting for an Switch Player release. Anything that competes with that is discussed before it's built.

So Switch Player uses only what nxnative documents for front ends (`docs/frontends.md`): the build script's command line, its `--progress json` events and exit codes, and each Game app's `Info.plist`, `launcher.json` and saved choices. Anything more it needs goes to nxnative as a pull request, never as a workaround here. Every nxnative internal Switch Player relied on would bring back the wait for an Switch Player release, as it did for NX Play.

One exception, and only while that pull request is open: Switch Player may read an undocumented nxnative detail where a failure could only change what Switch Player shows, never a Build or Play.

## Considered options

- **Draw the Game settings in Switch Player** from each Game app's `launcher.json`. Rejected: it duplicates the Game app's own Settings window, which nxnative keeps up to date.
- **Build with nxnative's developer path** (`--platform mac`) and rerun the command it prints, as NX Play does. Rejected: that path is for nxnative's developers, not front ends.
- **Keep nxnative's dependency list, option list or Xcode version in Switch Player.** Rejected: each would need an Switch Player release whenever nxnative changed it.

## Consequences

- Switch Player doesn't read game logs while a game runs: nxnative's log lines are for people, and the doc says not to treat them as an interface.
- Switch Player builds with whichever Xcode macOS has selected and never names a version. When nxnative needs another, its own error says which, and Switch Player shows that error.
- Four pull requests to nxnative, each with a fallback so Switch Player works without it:
  - **A switch that shows a Game app's Settings window** even when the player turned it off. Until then, **Settings…** needs that window left on.
  - **The build script's options in a form a program can read.** Until then, **Build Options…** is a text field passed to the script as typed.
  - **nxnative's dependencies as files** (Python version, Python packages, Homebrew packages), which Switch Player installs from after every Engine update. Until then, Switch Player uses NX Play's list.
  - **Documenting what tells a front end a Game app is Out of date**: the commit a Game app records, the folder holding each game's own files, and the shader version. Until then, Switch Player reads them under the exception above, and shows only a count of newer changes if any of them is missing.
