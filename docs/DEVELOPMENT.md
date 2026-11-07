# Developing and releasing Switch Player

The product decisions are in [DESIGN.md](DESIGN.md); rules for working on the maintainer's Mac are in [CLAUDE.md](../CLAUDE.md).

## Build from source

Use the pinned packages in `Application Files/requirements.txt` with the framework Python 3.13. Run all tests with:

```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest
```

Build and sign the app without installing or opening it:

```bash
scripts/build.sh
```

The path is printed on success. Builds go to `~/Library/Caches/SwitchPlayer-build`, not the repository: this repo is in an iCloud-synced folder, whose Finder metadata can make `codesign` fail. The script clears extended attributes before signing. `scripts/build.sh --adhoc` makes a local build that cannot update itself. Set `SWITCHPLAYER_BUILD_DIR` to use another temporary build folder.

The app icon's source is `Application Files/SwitchPlayer.svg`. After changing it, run `scripts/make_icon.py` with the framework Python: WebKit renders it (a window opens for a few seconds), then it writes `Application Files/SwitchPlayer.icns`, which the app bundle uses, and `docs/images/icon.png` for the README.

The README's screenshots come from the page preview's `readme` scenario (`?s=readme,light`), which holds a Build still part-way.

`SWITCHPLAYER_DATA_DIR` isolates app data, logs and save migration. Never run a real Build, Play a game or open the native app as part of routine development. Use the page preview and simulated services instead.

## Another nxnative checkout

Switch Player builds with its own clone of nxnative (`~/Library/Application Support/Switch Player/nxnative`), which it moves to upstream's latest at each Build. To build with a checkout of yours instead, for example to try local nxnative changes:

1. Quit Switch Player (it keeps its settings in memory and would overwrite an edit).
2. In `~/Library/Application Support/Switch Player/settings.json` (or `$SWITCHPLAYER_DATA_DIR/settings.json`), set the key to the checkout's folder; a leading `~` is fine:

```json
{ "nxnative_checkout": "~/dev/nxnative-mine" }
```

3. Open Switch Player. To go back to its own copy, delete the key (or set it to `null`) and reopen it.

What changes while it's set:

- Switch Player never fetches, resets or otherwise changes that folder. Builds use whatever is checked out, uncommitted edits included, so a Game app built from edits records a `DMCommit` ending in `+dirty`. Such a game never turns yellow; it shows only that nxnative has changes since its last Build, counted from the commit under the edits.
- Out of date and **Check for nxnative Updates…** compare built games with that checkout's current commit, without fetching.
- The folder must already hold `scripts/build_ipa_from_nsp.py`; a Build otherwise fails at once with "There's no nxnative build script in <folder>".
- Switch Player's own Python environment is still used, with its own package list (`Application Files/nxnative-python/pyproject.toml`), not the checkout's.

## Release checklist

Merging a pull request is **not** a release. Installed copies change only when a GitHub release is published.

### Before building

- [ ] Everything going out is merged into `main`; local `main` matches GitHub.
- [ ] Raise `VERSION` in `Application Files/main.py`. It must be newer than the latest release.
- [ ] The full test suite passes.
- [ ] `security find-identity -v -p codesigning` lists a valid Apple Development identity from team `XT4LT7MS6Y`. Updates signed by another team are refused.
- [ ] DESIGN.md, the README and other user-facing instructions match what is shipping.

### Build and check the disk image

- [ ] `scripts/package.sh` creates `dist/Switch-Player-<version>.dmg`. This is the release's only asset. Finder lays out its window and may ask for Automation permission.
- [ ] Open the disk image: Switch Player and Applications are beside the arrow, with the first-launch card readable. Do not open the app during routine verification.
- [ ] Check the version, architecture, resources and signature:

```bash
M=$(hdiutil attach -nobrowse -readonly -noautoopen dist/Switch-Player-<version>.dmg | grep -o '/Volumes/.*$')
defaults read "$M/Switch Player.app/Contents/Info" CFBundleShortVersionString
codesign --verify --deep --strict "$M/Switch Player.app"
codesign -dvv "$M/Switch Player.app" 2>&1 | grep TeamIdentifier
file "$M/Switch Player.app/Contents/MacOS/Switch Player"
hdiutil detach "$M"
```

The bundle identifier must be `com.digitaln.switchplayer`, the architecture arm64, and the minimum macOS version 26.6. The page, Brewfile, Python package list and third-party notices must be in `Contents/Resources`.

### Test updating, if the updater, packaging or signing changed

- [ ] Use a separate test copy with its own bundle identifier and `SWITCHPLAYER_DATA_DIR`; never replace the maintainer's installed copy.
- [ ] A higher-version, same-team signed app downloads, verifies, swaps and relaunches from a stand-in release.
- [ ] A modified app, an ad-hoc app, a different team and a release whose internal version is not newer are refused.
- [ ] A running or waiting Build holds automatic relaunch.
- [ ] The toast shows each step: Checking for updates…, Downloading with its %, Checking the download…, ready with Install Now, Installing…, then "Updated to …" in the new copy.
- [ ] Delete the test copy, data folder and test disk images afterwards.

### Publish

- [ ] Write notes for the current version, including installation instructions.
- [ ] Publish tag `v<version>` from the commit that was built:

```bash
gh release create v<version> dist/Switch-Player-<version>.dmg --title "Switch Player <version>" --notes-file notes.md
```

- [ ] Turn the previous release into a draft so the page lists only the current one: `gh release edit v<previous> --draft=true`.
- [ ] Check `https://github.com/DigitalN/SwitchPlayer/releases/latest` and the downloaded image's SHA-256 against the local file.
- [ ] An older installed copy finds the update through **Check for Updates…** or its next launch. Its log shows `Downloading`, `ready to install`, `Installed …; relaunching`, then the new copy starting.

## How updates work

`Application Files/updater.py` and the bundle/relaunch helpers in `launch.py` are adapted from Mac Engine Room.

- **When:** at every launch, and in an app left open, once 24 hours have passed since the last check that got through (`last-update-check` in the data folder): an hourly timer looks, and so does a look one minute after the Mac wakes. A check that can't reach GitHub doesn't count: the next hourly look tries again, without a toast. **Check for Updates…** checks immediately; during an automatic check, it makes that check the player's. The launch after an App update skips its check (`installed-update` in the data folder: the toast says "Updated to …" instead).
- **Feed:** GitHub's latest-release API, through `/usr/bin/curl` and the system certificates. Drafts and pre-releases are excluded by GitHub. The release needs a `.dmg` asset.
- **Verification:** the app is found by bundle identifier, not filename. `codesign --verify --deep --strict` requires that identifier, an Apple generic anchor and this copy's team. The version inside the image must be newer than the installed version.
- **The toast:** `Updater.toast()` goes to the page with the Library each second, and a new step nudges the page to ask at once. The page writes the words (`toastView` in `index.html`); a result that fades shows for four seconds from when the page first sees it, and is dropped after fifteen if no page did.
- **Installation:** automatic updates wait for ten seconds without keyboard or mouse input, or for **Install Now** in the toast, and for the Build queue to be idle. "Installing…" shows for a second and a half before the app quits. The app is swapped with one `NSFileManager` replacement call and relaunched after the old process exits. The relaunch retries once if Launch Services has not caught up.
- **Off:** source checkouts, ad-hoc copies, apps outside Applications and apps that cannot replace themselves do not self-update. Check for Updates… opens the releases page instead. The last two say "Updates are off: …" in the toast at launch.
- **Testing:** `SWITCHPLAYER_UPDATE_FEED` can point to a stand-in JSON response, including a `file://` URL. Signature checks still apply. Relaunch preserves `SWITCHPLAYER_DATA_DIR` and `SWITCHPLAYER_UPDATE_FEED` for an isolated test copy.

Releases use an Apple Development certificate, not Developer ID or notarization. The first-launch card explains **System Settings → Privacy & Security → Open Anyway**. Renew the certificate before it expires and keep the same team.
