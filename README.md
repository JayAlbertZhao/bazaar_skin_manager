# The Bazaar Skin Manager

A Windows manager for importing, deploying, updating, and removing external
skin packs for **The Bazaar**.

This repository contains only the manager, its runtime adapter, pack contracts,
tests, and release tooling. It does **not** contain or distribute character
art, portraits, voice recordings, generated media, or third-party skin packs.
Creators distribute those packs separately as ZIP files.

## Features

- Detects Steam installations across registered and conventional library paths.
- Imports complete visual-and-audio ZIP packs.
- Supports individual image and audio slot editing.
- Leaves unfilled slots on the original game assets.
- Supports PNG, JPEG, WebP, BMP, WAV, MP3, FLAC, Ogg, M4A, AAC, and Opus input.
- Converts supported audio through `ffmpeg` when available.
- Removes a configurable colour screen from imported images.
- Preserves animation source sets for future runtime adapters.
- Applies supported image slots before game startup to avoid late replacement.
- Deploys the runtime and external pack reversibly.
- Keeps verified backups of native files touched by preloading.
- Detects Steam updates and fails closed when the authorized fingerprint changes.
- Restores manager-owned changes during undeploy or uninstall.
- Starts the game through Steam.

The current public runtime adapter has verified deployment support for Mak's
default skin on Steam build `24001960`. Other heroes remain visible in the
catalog but are disabled until their adapters have been verified.

## Install

Download one of the manager-only artifacts from
[GitHub Releases](https://github.com/JayAlbertZhao/bazaar_skin_manager/releases):

- `TheBazaarModManager-Setup-<version>.exe`
- `TheBazaarModManager-Portable-<version>.zip`

The installer is per-user and does not require administrator rights. If a
release is unsigned, Windows may display a reputation warning; verify the
published SHA-256 before running it.

The runtime requires a working BepInEx installation. BazaarPlusPlus is the
currently tested way to provide it. The manager preserves unrelated BepInEx
plugins.

## First run

1. Start **The Bazaar Skin Manager**.
2. Confirm that the detected game folder points to the Steam installation.
3. Select a hero and supported skin.
4. Drag a separately distributed skin ZIP onto the package drop zone.
5. Review the visual and audio slot indicators.
6. Close the game.
7. Select **Deploy**.
8. Use **Start Game** or launch the game from Steam.

Use **Undeploy / restore original** before removing a pack or when returning to
the unmodified game.

## External pack contract

A complete pack ZIP contains one directory with a single `mod.json`:

```text
my-skin-pack/
  mod.json
  asset-index.json
  assets/
    portrait_gameplay.png
    standing_overlay.png
    hero_select.png
  audio-manifest.json
  audio/
    line-01.wav
```

`mod.json` declares:

- a stable pack id, display name, and semantic version;
- the target game, hero, and skin;
- only the visual slots supplied by the pack;
- an optional `audio-manifest.json`;
- optional animation authoring sources.

Every referenced file must remain inside the pack directory and may be covered
by `asset-index.json` SHA-256 metadata. The manager rejects path traversal,
malformed manifests, unsupported deployment modes, unsafe native targets, and
oversized ZIP extraction.

Detailed authoring behavior is documented in
[docs/mod-manager-studio.md](docs/mod-manager-studio.md). Audio validation is
documented in [docs/audio-asset-contract.md](docs/audio-asset-contract.md).

## Local data

Manager-owned state is stored below:

```text
%LOCALAPPDATA%\BazaarSkinManager\TheBazaar\
  manager\
    install-manifest.json
    runtime-compatibility.json
    native-backups\
    workspaces\
  mods\
```

The install manifest records exactly which runtime, pack, compatibility file,
and native bundle patches belong to the manager. Undeploy removes or restores
only those recorded paths.

## Development

Requirements:

- Windows 10 or newer
- Python 3.12
- .NET Framework/MSBuild compatible with the runtime project
- local The Bazaar managed assemblies for runtime compilation
- Inno Setup for installer builds

Create an isolated environment and install the build dependencies:

```powershell
conda create -n bazaar-skin-manager python=3.12
conda activate bazaar-skin-manager
python -m pip install -r manager\requirements-build.txt
```

Run the Python suite:

```powershell
python -m unittest discover -s tests -v
```

Build the runtime and manager:

```powershell
.\build.ps1 -Configuration Release
.\build-manager.ps1 -Version 0.9.61
.\build-installer.ps1 -Version 0.9.61
.\package-manager-portable.ps1 -Version 0.9.61
```

Useful source commands:

```powershell
python tools\bazaar_skin_manager.py detect
python tools\bazaar_skin_manager.py status
python tools\bazaar_skin_manager.py doctor
python tools\bazaar_skin_manager.py --pack C:\path\to\pack validate-pack
python tools\bazaar_skin_manager.py --pack C:\path\to\pack plan-install
```

## Release policy

GitHub releases are manager-only. CI packages:

- the manager executable;
- the per-user installer;
- the portable ZIP;
- SHA-256 metadata.

No skin pack is attached to a GitHub release or embedded in the installer.
Release and verification details are in
[docs/release-runbook.md](docs/release-runbook.md).

## Legal

This is an unofficial community tool and is not affiliated with or endorsed by
Tempo or The Bazaar. Users and pack creators are responsible for ensuring that
they have the rights to any content they import or distribute.
