# Developing and releasing Switch Player

The product decisions are in [DESIGN.md](DESIGN.md); current implementation and test boundaries are in [HANDOFF.md](HANDOFF.md).

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

`SWITCHPLAYER_DATA_DIR` isolates app data, logs and save migration. Never run a real Build, Play a game or open the native app as part of routine development. Use the page preview and simulated services instead.

## Release checklist

Merging a pull request is **not** a release. Installed copies change only when a GitHub release is published.

### Before building

- [ ] Everything going out is merged into `main`; local `main` matches GitHub.
- [ ] Raise `VERSION` in `Application Files/main.py`. It must be newer than the latest release.
- [ ] The full test suite passes.
- [ ] `security find-identity -v -p codesigning` lists a valid Apple Development identity from team `XT4LT7MS6Y`. Updates signed by another team are refused.
- [ ] The handoff and user-facing instructions match what is shipping.

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
- [ ] Delete the test copy, data folder and test disk images afterwards.

### Publish

- [ ] Write notes for the current version, including installation instructions.
- [ ] Publish tag `v<version>` from the commit that was built:

```bash
gh release create v<version> dist/Switch-Player-<version>.dmg --title "Switch Player <version>" --notes-file notes.md
```

- [ ] Turn the previous release into a draft so the page lists only the current one: `gh release edit v<previous> --draft=true`.
- [ ] Check `https://github.com/DigitalN/SwitchPlayer/releases/latest` and the downloaded image's SHA-256 against the local file.
- [ ] An older installed copy finds the update through **Check for Updates…** or the next wake. Its log shows `Downloading`, `ready to install`, `Installed …; relaunching`, then the new copy starting.

## How updates work

`Application Files/updater.py` and the bundle/relaunch helpers in `launch.py` are adapted from Mac Engine Room.

- **When:** once a day, one minute after the Mac wakes. **Check for Updates…** checks immediately. A failed network request does not count as the day's check.
- **Feed:** GitHub's latest-release API, through `/usr/bin/curl` and the system certificates. Drafts and pre-releases are excluded by GitHub. The release needs a `.dmg` asset.
- **Verification:** the app is found by bundle identifier, not filename. `codesign --verify --deep --strict` requires that identifier, an Apple generic anchor and this copy's team. The version inside the image must be newer than the installed version.
- **Installation:** automatic updates wait for ten seconds without keyboard or mouse input and for the Build queue to be idle. The app is swapped with one `NSFileManager` replacement call and relaunched after the old process exits. The relaunch retries once if Launch Services has not caught up.
- **Off:** source checkouts, ad-hoc copies, apps outside Applications and apps that cannot replace themselves do not self-update. Check for Updates… opens the releases page instead.
- **Testing:** `SWITCHPLAYER_UPDATE_FEED` can point to a stand-in JSON response, including a `file://` URL. Signature checks still apply. Relaunch preserves `SWITCHPLAYER_DATA_DIR` and `SWITCHPLAYER_UPDATE_FEED` for an isolated test copy.

Releases use an Apple Development certificate, not Developer ID or notarization. The first-launch card explains **System Settings → Privacy & Security → Open Anyway**. Renew the certificate before it expires and keep the same team.
