# Changelog

## 1.0.0

- Add verified default-skin adapters for Mak, Vanessa, Pygmalien, Dooley, and
  Jules, including audited native Texture2D targets for the current Steam
  build.
- Let the asset generator select any verified hero target and reuse one
  deterministic rendering recipe with narrow per-hero output overrides.
- Publish the exact adapter ids, versions, targets, and hashes in
  `manager-build.json` so companion tools can check compatibility before
  modifying a Manager workspace.
- Replace cascaded per-slot errors for an unsupported hero with one actionable
  update/target compatibility error.
- Keep verified adapters fail-closed: asset packs still cannot supply or
  override Manager deployment contracts.
- Generalize the request-gated central-standing diagnostic to every target
  hero and verify attach, idempotence, and native-state restoration in-game.
- Make runtime audit validation understand deploy-time patched inspector
  backgrounds and explicitly scoped local-player portrait loads.
- Restore the proven Mak portrait layering contract for generated packs:
  `portrait_gameplay` is a transparent foreground, while
  `portrait_background` remains the separate lower layer. This removes the
  unwanted background in the hero-select preview and keeps the in-match HUD
  frame above the portrait art.
- Ship the Skin Manager and the independent Asset Generator as the two public
  software components. Character and artwork packs remain outside the GitHub
  release and are distributed separately.
- Keep the Asset Generator pipeline actions pinned above the window bottom and
  add vertical scrolling to both authoring tabs, including minimum-window
  layout validation in the frozen executable self-test.
- Accept sparse monochrome icons produced by the included deterministic icon
  presets without weakening the transparent-border guard.
- Default new Asset Generator projects to writing the generated pack beside
  the Asset Generator executable.

## 0.9.63

- Fix Windows startup failures caused by a packaged Tcl DLL and Tcl library
  data coming from different patch versions.
- Make the frozen-manager release self-test initialize Tcl/Tk so an invalid
  GUI bundle fails during packaging instead of on the user's machine.
- Keep the runtime adapter, skin assets, audio routing, and deferred badge
  authoring pipeline unchanged from 0.9.62.

## 0.9.62

- Restrict PvP transition art, board portraits, and late-loaded portrait
  replacements to the local player; opponent visuals retain native assets.
- Give the local board portrait a dedicated background layer sourced from the
  replacement artwork instead of exposing Mak's native portrait background.
- Normalize the external Kotone voice pack against the corresponding original
  Mak routes and retain native audio as the fail-open fallback.
- Display the manager version in both the window title and application header.
- Keep the deferred hero-select badge authoring pipeline excluded.

## 0.9.61

- Detect an existing per-user installation through the stable Inno Setup
  application identity and upgrade it in place instead of creating a parallel
  installation.
- Preserve manager workspaces and deployed-mod state while replacing the
  installed manager binary and release metadata.
- Publish the matching version section from this changelog on every GitHub
  Release; a missing section now fails the release job.
- Keep the runtime behavior and asset scope of 0.9.6 unchanged. The deferred
  hero-select badge authoring pipeline remains excluded.

## 0.9.6

- Keep `PvpScreen`, `EndOfDayScreen`, and other exact SkinEdit placements
  independent from the central hero-selection reconciler, preventing an
  attached replacement from reverting to the native hero during PvP entry.
- Discover hero/skin adapters through a fail-closed registry and preload the
  verified gameplay portrait route through its exact native Texture2D target.
- Promote the PvP result-speaker selection introduced in 0.9.5 to the stable
  release line.

## 0.9.5 (experimental)

- Choose exactly one PvP result speaker per combat: the local player or the
  opponent, with equal probability when both can speak.
- Preserve the native opponent-only behavior outside the target hero skin and
  fall back to the local player for non-verbal opponents.
- Keep this behavior on the `experimental` branch; stable `main` remains on
  the 0.9.3 audio-routing semantics.

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
