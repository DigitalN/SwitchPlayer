# Switch Player

A Mac app that builds and plays the player's own Switch games through nxnative, without the command line. Everything about how a game runs is left to nxnative.

## Language

### Games and where they live

**Game**:
One Switch or Switch 2 title the player owns: its base game, plus any update and DLC.
_Avoid_: ROM

**Library folder**:
The folder the player chooses to hold their Games, one Game folder each.
_Avoid_: watched folder, games folder

**Game folder**:
A subfolder of the Library folder holding one Game's files. A single `.nup` file in the Library folder counts as one too.
_Avoid_: NSP folder, ROM folder

**Game app**:
The Mac app nxnative makes from a Game folder, kept in that Game folder. It carries the nxnative code it was built with.
_Avoid_: build, port, wrapper

### Building

**Build**:
Making a Game folder's Game app, for the first time or again, with the latest nxnative code. Only the player starts one.
_Avoid_: pack, compile, rebuild (a rebuild is a Build). The one place the word stays is the page's own wording: the ⋯ item **Rebuild** and the Out of date line "Your version is out of date, consider rebuilding", which the page and Check for nxnative Updates… use, saying it to the player the way people do. Code, docs and PRs say Build.

**Stop**:
Ending a Build before it finishes, or taking a waiting one out of the queue. The Game app from the last finished Build stays playable.
_Avoid_: cancel, abort

**Build options**:
The choices nxnative offers for one Build of one Game, left at nxnative's defaults unless the player sets them.
_Avoid_: settings, pack options

**Build log**:
What one Build of a Game printed, as Switch Player keeps it: its preparation, nxnative's stages and messages, and how it ended. The **Build Log pane** beside the Library shows it as it grows.
_Avoid_: output, console, game log (that's the Game app's own log, Show Log)

**Built in NX Play**:
A Game that NX Play built with nxnative's developer path: its build folder is there, with NX Play's own files, but there's no Game app to Play until Switch Player Builds one.
_Avoid_: build incomplete, half-built

**Out of date**:
A Game app that nxnative has since changed something for specifically (that Game's own files, or anything that remakes its shaders), or whose Game folder has gained files since its Build. Other newer nxnative changes are counted, not warned about.
_Avoid_: stale, old

### Playing

**Play**:
Starting a Game app straight into its game, with the player's saved Game settings.

**Game settings**:
The choices a Game app offers before its game starts (frame rate, render scale, …), kept by that Game app.
_Avoid_: options, preferences, Build options

**Settings window**:
The window a Game app shows before its game starts, where the player changes its Game settings.

### nxnative and updates

**nxnative copy**:
The nxnative checkout Switch Player builds with: its own clone of upstream nxnative, or a checkout the player points it at, which Switch Player then never changes.
_Avoid_: engine, repo

**Engine update**:
Bringing Switch Player's own nxnative copy up to upstream nxnative's latest code.
_Avoid_: update (on its own)

**App update**:
A new release of Switch Player itself, which Switch Player installs.
_Avoid_: update (on its own)

**Update toast**:
The pill at the bottom of the window that says what an App update is doing (checking, downloading, installing) and how it ended.
_Avoid_: notification (a Build's end is a macOS notification), banner
