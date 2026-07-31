# Release runbook

## What GitHub publishes

A version tag such as `v0.9.5-experimental` produces:

- GitHub's automatic source ZIP and source tarball for the tag;
- `TheBazaarModManager-Setup-0.9.5.exe`;
- the installer's SHA-256 file;
- `TheBazaarModManager-Portable-0.9.5.zip`.

The public workflow does not package a skin's art or audio. Complete asset
packs remain ordinary ZIP inputs to the manager and can be distributed through
any separate channel.

## Maintainer release

1. Update the manager and runtime-adapter versions independently, then update
   the changelog. Asset-pack versions remain in each external `mod.json`.
2. Copy the audited runtime DLL to
   `manager/runtime/BazaarSkinManager.Runtime.dll`.
3. Run `python -m unittest discover -s tests -v`.
4. Run `build-manager.ps1`, `build-installer.ps1`, and the installer smoke
   test.
5. Commit the source, create an immutable semantic tag, and push it:

   ```powershell
   git tag v0.9.5-experimental
   git push origin experimental v0.9.5-experimental
   ```

6. The release workflow builds on `windows-latest`, uploads its workflow
   artifact, and creates or updates the GitHub Release.
7. Download the published installer, compare its SHA-256, and test it on a
   clean Windows user account before announcing it.

For Authenticode signing, configure repository secrets
`WINDOWS_SIGNING_CERT_BASE64` and `WINDOWS_SIGNING_CERT_PASSWORD`. The workflow
then signs both the manager and final installer and refreshes their hashes.
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
