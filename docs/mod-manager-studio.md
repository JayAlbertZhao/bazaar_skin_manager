# The Bazaar Skin Manager

The manager has two responsibilities:

1. author a data-only skin pack from individual files or ZIP packages;
2. deploy or undeploy one verified pack through the reversible BepInEx runtime.

Unfilled slots are deliberately absent from `mod.json`. The runtime then keeps
the original game asset for that surface.

## User flow

1. Select a hero.
2. Select one of the skins known for that hero.
3. Create or open a workspace.
4. Import a complete ZIP, an audio-production ZIP, or individual assets.
5. Review every visual/audio slot. Empty rows say **Original**.
6. Close The Bazaar and press **Deploy**.
7. Press **Start Game** to launch app `1617400` through Steam.
8. Press **Undeploy / restore original** to remove the manager-owned runtime
   and pack and restore any manager-owned native bundle backup.

The manager checks the Steam registry, every mounted drive's conventional
Steam folders, and each discovered `steamapps/libraryfolders.vdf`. The detected
game path and Steam build are shown in the sidebar. If `steam.exe` is available,
Start Game uses `steam.exe -applaunch 1617400`; otherwise it uses the registered
`steam://rungameid/1617400` protocol.

The current release has a verified runtime adapter for Mak's default skin.
Vanessa, Pygmalien, Dooley, and Jules are visible in the catalog so their
future adapters and packs use the same UI, but Deploy is disabled for them
until their runtime routes have been audited.

## Complete package

A complete package is a ZIP containing `mod.json` and `asset-index.json` at its
root (one enclosing directory is also accepted). Visual and audio files may be
placed below any safe relative path, but the normal layout is:

```text
mod.json
asset-index.json
assets/
  portrait_gameplay.png
  standing_overlay.png
  hero_select.png
  hero_icon_small.png
  portrait_small.png
  store_image.png
  collection_list.png
  collection_details.png
  marketplace_list.png
  marketplace_details.png
  daily_weekly.png
audio-manifest.json
audio/
  *.wav
animation/
  optional authoring sources
```

The ZIP importer rejects absolute paths, `..` traversal, multiple competing
`mod.json` files, missing assets, hash mismatches, and archives expanding past
2 GiB.

## First-frame native slots

Every slot whose native source is an independently verified Texture2D is
written into its exact UnityFS bundle during Deploy. The current Mak adapter
preloads:

- `portrait_gameplay`;
- `store_image`, `marketplace_list`, and `marketplace_details`;
- `collection_list` and `daily_weekly`;
- `hero_select`;
- `hero_icon_small`.

Aliases sharing one native Texture2D are validated for identical input and
merged into a single bundle rewrite. This removes first-frame lag without
writing the same bundle several times.

Deploy requires a supported SHA-256 for the untouched target bundle, creates
an external backup, rebuilds and reopens the patched bundle for verification,
then atomically replaces the game file. Undeploy restores only when the current
file is still the manager-owned patched hash; a Steam-updated file is never
overwritten with an older backup.

Three visual surfaces remain runtime-managed for structural reasons:
`portrait_small` shares its native source with collection/daily assets while
the game ignores the loader's size flag; `collection_details` and
`standing_overlay` are GameObject/Spine presentations rather than standalone
flat textures. Audio also remains runtime-managed because FMOD banks require a
format-compatible bank rebuild rather than a byte-level WAV substitution.

## Loose visual ZIP

If a ZIP has no `mod.json`, image filenames are matched to the visual slot ids
shown above. Recognized files are converted to RGBA PNG. Missing filenames
remain original.

An individual image can also be:

- dropped on its slot;
- selected with **Import**;
- pasted from the clipboard with **Paste**.

When **Remove colour screen on import** is enabled, the selected `#RRGGBB`
colour becomes transparent. Tolerance controls the colour distance and the
edge receives a small fixed feather.

## Audio input

Per-line imports accept WAV, MP3, FLAC, OGG, M4A, AAC, and Opus. Files already
encoded as PCM16 mono 22050 Hz WAV are copied directly. Other formats are
converted with `ffmpeg` when it is available; otherwise the UI requests a
runtime-ready WAV.

Whole audio ZIPs support:

- a complete skin pack with `audio-manifest.json`;
- a production manifest named `*-voice-assets.json` whose schema follows
  `<producer>-voice-assets/v<major>`;
- loose audio files named from a logical slot slug.

Every route is optional. An empty route falls back to the original FMOD event.

## Skeleton and dynamic assets

The package/UI accepts:

- Spine source sets: `.skel` or skeleton `.json`, `.atlas`, and textures;
- Unity AssetBundle authoring files: `.bundle` or `.assetbundle`.

They are preserved under the `animation` manifest extension. Runtime `0.4.x`
continues using the static `standing_overlay` fallback: the prefab/skeleton
playback adapter is not yet verified and the UI labels these files as
authoring sources rather than claiming they animate in game. This is the next
runtime release track, not a silent partial implementation.

## Build

Source run:

```powershell
.\launch-manager.ps1
```

Standalone Windows build:

```powershell
.\build-manager.ps1
```

The executable is written to:

```text
dist/manager/TheBazaarModManager.exe
```

Portable release:

```powershell
.\package-manager-portable.ps1 -Version 0.9.1
```

This writes `dist/TheBazaarModManager-Portable-0.9.1.zip`, containing only the
standalone executable, hashes, and a quick-start guide. Skin packs are imported
from separately distributed ZIP files.
