# Changelog

## 0.9.4

- Keep exact `PvpScreen`, new-day and other SkinEdit overlays independent
  from the main hero-selection reconciler. This fixes the versus portrait
  reverting to the native hero after the replacement had already attached.
- Add a regression guard for non-central `HeroSelectDisplay` cleanup.
- Bridge the game's local-hero PvP result call when the opponent is verbal,
  allowing the existing exact `PvP_VictoryDefeat` victory and defeat routes
  to run instead of remaining unreachable behind the native `NonVerbal` gate.

## 0.9.3

- Prevented specialized standing/collection overlays from matching gameplay
  portrait sprites through broad substring routes.
- Restored the gameplay portrait to the verified runtime Sprite route so its
  256 PPU, full-rect geometry, and alpha envelope are applied atomically.
- Made exact asset-name matches outrank fuzzy routes regardless of manifest
  ordering.

## 0.9.2

- Track manager, runtime-adapter, and external asset-pack versions and hashes
  as independent deployment components.
- Bundle verified runtime metadata with frozen manager releases.
- Restrict central standing-art reconciliation to the actual hero-select
  hierarchy so gameplay portrait surfaces cannot receive a delayed overlay.
- Preserve deploy-time Addressables CRC repair and reversible native backups.

## 0.9.1

- Repositioned the repository as a manager-only public project.
- Removed bundled skin manifests, media metadata, and asset-production tooling.
- Moved verified hero routing into a data-only adapter contract.
- Renamed runtime namespaces, storage paths, plugin ids, and binaries to the
  generic Bazaar Skin Manager identity.
- Made source CLI install commands require an explicit external pack.
- Kept GitHub installer and portable releases free of skin art and audio.

## 0.9.0

- Added the Windows Skin Manager with partial visual/audio slot editing.
- Added complete-pack ZIP import and export.
- Added reversible deploy and restore for verified Mak default-skin assets.
- Added deploy-time Unity bundle texture replacement for first-frame surfaces.
- Added Steam installation discovery across registry, library folders, and
  conventional paths on mounted drives.
- Added manual game-folder selection and one-click Steam launch.
- Added per-user Windows installer, portable manager build, release hashes,
  safe application-uninstall restoration, and GitHub Release automation.
- Added optional Authenticode signing in the release workflow.
