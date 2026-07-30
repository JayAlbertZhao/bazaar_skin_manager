# Changelog

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
