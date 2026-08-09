# Release runbook

## Version and completion policy

Every completed feature must advance the application version after acceptance
tests pass:

- a small compatible update increments the patch version;
- a substantial feature update increments the minor version;
- a complete architectural rewrite increments the major version.

A version is not complete when only source code or local binaries exist. The
same release operation must also:

1. update every manager, runtime, installer, packaging, test, and changelog
   version surface;
2. publish the accepted source commit and immutable version tag to GitHub;
3. publish both the Setup EXE and portable ZIP, with SHA-256 sidecars, in the
   GitHub Release;
4. retain locally accessible copies of both published binary artifacts for
   handoff;
5. upgrade the maintainer's installed copy, launch that installed executable,
   verify that its window reports the new version, and close it cleanly.

Do not create the release tag before the test, build, and local packaging gates
have passed.

## What GitHub publishes

A version tag such as `v1.0.0` produces:

- GitHub's automatic source ZIP and source tarball for the tag;
- `TheBazaarModManager-Setup-1.0.0.exe`;
- the installer's SHA-256 file;
- `TheBazaarModManager-Portable-1.0.0.zip` and its SHA-256 file.

Both binary artifacts expose the complete integrated workflow through the single
`TheBazaarModManager.exe`; no separate Asset Generator or Spine Manager EXE is
published.

The public workflow does not package a skin's art or audio. Complete asset
packs remain ordinary ZIP inputs to the manager and can be distributed through
any separate channel.

## Maintainer release

1. Update the manager, runtime adapter, and integrated authoring component versions, then
   update the changelog. Asset-pack versions remain in each external
   `mod.json`.
2. Copy the audited runtime DLL to
   `manager/runtime/BazaarSkinManager.Runtime.dll`.
3. Run `python -m unittest discover -s tests -v`.
4. Run `build-manager.ps1`, `build-installer.ps1`,
   `package-manager-portable.ps1`, and the smoke tests.
5. Commit the source, create an immutable semantic tag, and push it:

   ```powershell
   git tag v1.0.0
   git push origin main v1.0.0
   ```

6. The release workflow builds on `windows-latest`, uploads its workflow
   artifact, and creates or updates the GitHub Release with the matching
   version section from `CHANGELOG.md`. A missing changelog section fails the
   release instead of publishing an empty release page.
7. Download both published binaries and compare their SHA-256 sidecars.
8. Upgrade the local installed copy with the published installer, open it,
   verify its displayed version, and close it cleanly before announcing the
   release. A clean Windows user-account test remains required for major
   releases and installer changes.

For Authenticode signing, configure repository secrets
`WINDOWS_SIGNING_CERT_BASE64` and `WINDOWS_SIGNING_CERT_PASSWORD`. The workflow
then signs the manager and final installer and refreshes their hashes.
Without these secrets, Windows reports the result as unsigned and SmartScreen
may warn.

## New-user path

1. Download the Setup EXE from GitHub Releases and verify its SHA-256.
2. Install per-user; administrator rights are not required.
3. Start the manager from the Start Menu.
4. On first run, the manager checks Steam registry data,
   `libraryfolders.vdf`, and conventional Steam folders on mounted drives.
5. If detection fails, use **Locate game manually** and select the folder
   containing `TheBazaar.exe` and `TheBazaar_Data`.
6. Import a compatible complete asset-pack ZIP by drag-and-drop or browse.
7. Close The Bazaar and press **DEPLOY**.
8. Press **START GAME**. Launch always goes through Steam app `1617400`.
9. After a Steam game update, reopen the manager and inspect status before
   redeploying.
10. Use **Undeploy / restore original** before removing a mod. The Windows
    uninstaller invokes the same restoration command before deleting the app.

## Failure handling

- A missing game never triggers a guessed direct executable launch.
- An incomplete folder cannot be selected or deployed.
- Unfilled image/audio slots retain original game behavior.
- Native assets are replaced only when their known original hash matches.
- A Steam-updated native file is not overwritten with an older backup.
- Authoring workspaces under Local AppData survive manager upgrades and normal
  uninstallation.
- If application uninstall cannot restore because the game is open, reinstall
  the same manager version, close the game, then use **Restore original**.
