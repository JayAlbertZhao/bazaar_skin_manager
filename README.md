# The Bazaar Skin Manager

A Windows manager for importing, deploying, updating, and removing external
skin packs for **The Bazaar**.

This repository contains the Skin Manager, the deterministic Asset Generator,
the Spine Manager, runtime adapters, pack contracts, tests, and release tooling. It does
**not** contain or distribute character art, portraits, voice recordings,
generated media, or third-party skin packs. Creators distribute those packs
separately as ZIP files.

The source also includes an adapter-driven deterministic raster builder; see
[`docs/deterministic-skin-pack-builder.md`](docs/deterministic-skin-pack-builder.md).
Run `launch-asset-generator.ps1` for the independent desktop generator. It
builds a complete ZIP from the three declared author inputs, imports that ZIP
into the existing Skin Manager workspace format, and delegates reversible
deployment to the Manager rather than implementing a second installer.
Its hero-select compositor uses a locally extracted, hash-verified copy of the
installed game's badge frame; those official pixels are not stored here.
Fidelity-critical extraction rules are recorded in
[`docs/asset-pipeline-invariants.md`](docs/asset-pipeline-invariants.md).

## Components

This repository publishes one manager application. The main application contains
the complete workflow. The older standalone creator entry points remain in source
for development and compatibility testing, but are not separate release programs:

- **Skin Manager** — the primary application. It provides skin deployment,
  skin-pack and first-class asset management, embedded deterministic skin
  creation, Spine animation import, and Steam/game settings. Its desktop entry
  point is `tools/bazaar_skin_manager_ui.py`.
- **Asset Generator source entry point** — development access to the same
  deterministic creator pipeline embedded in the manager.
- **Spine Manager source entry point** — development access to the same Spine
  import and verification pipeline embedded in the manager.

The Skin Manager is the only deployment control center. Every change to game
files goes through its verified, reversible deployment transaction.

## Features

- Detects Steam installations across registered and conventional library paths.
- Imports complete visual-and-audio ZIP packs.
- Supports individual image and audio slot editing.
- Retargets one portable logical voice set across all eight supported heroes;
  source filenames such as `MakIdle1.wav` do not bind the pack to Mak.
- Provides automatic generation and a separate per-slot authoring mode.
- Lets each visual slot keep independent image, position, and scale settings;
  background-capable slots can compose separately adjustable background and
  character layers.
- Uses one shared Manager workspace cache for both creation modes. Authors can
  switch between the live default draft and per-slot editing at any time;
  unchanged switches retain existing fine-tuning, while the default mode can
  still publish directly to the skin library.
- Leaves unfilled slots on the original game assets.
- Supports PNG, JPEG, WebP, BMP, WAV, MP3, FLAC, Ogg, M4A, AAC, and Opus input.
- Converts supported audio through `ffmpeg` when available.
- Removes a configurable colour screen from imported images.
- Derives audited flat-texture slots through deterministic, offline recipes.
- Preserves animation source sets for future runtime adapters.
- Applies supported image slots before game startup to avoid late replacement.
- Deploys the runtime and external pack reversibly.
- Keeps verified backups of native files touched by preloading.
- Detects Steam updates and fails closed when the authorized fingerprint changes.
- Checks the public GitHub Releases feed for stable manager updates and only
  launches a version-matched installer after its published SHA-256 and size
  have been verified.
- Builds a local, redacted diagnostic report for one-click copying so users can
  paste it into IM feedback; logs are never uploaded by the application.
- Restores manager-owned changes during undeploy or uninstall.
- Starts the game through Steam.

The current adapters have verified deployment support for the default skins
of Mak, Vanessa, Pygmalien, Dooley, Jules, Stelle, Karnok, and The Dragons
(Rin & Jin). The original seven adapters support Steam builds `24001960` and
`24570932`; The Dragons first appears on build `24570932`.
Additional skins remain visible in the catalog but are disabled until their
adapters have been verified.
The update and adapter-verification procedure is recorded in
[`docs/game-build-compatibility.md`](docs/game-build-compatibility.md).

## Install

Download the software artifacts from
[GitHub Releases](https://github.com/JayAlbertZhao/bazaar_skin_manager/releases):

- `TheBazaarModManager-Setup-<version>.exe`
- `TheBazaarModManager-Portable-<version>.zip`

The installer and portable ZIP contain a single manager executable. Skin creation
and Spine animation import are already integrated into that executable. The animation
page accepts complete Spine 4.1/4.2 ZIP packages, including multi-page atlases.

The installer is per-user and does not require administrator rights. If a
release is unsigned, Windows may display a reputation warning; verify the
published SHA-256 before running it.

The runtime uses BepInEx 5. The manager carries the pinned official Windows
x64 BepInEx 5.4.23.5 archive and installs it automatically on the first deploy
when no loader is present. BazaarPlusPlus is not required and is not installed.
Existing compatible BepInEx installations and unrelated plugins are preserved.

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
.\build.ps1 -Version 1.4.5
.\build-manager.ps1 -Version 1.4.5
.\build-asset-generator.ps1 -Version 1.4.5
.\build-spine-manager.ps1 -Version 1.4.5
.\build-installer.ps1 -Version 1.4.5
.\package-manager-portable.ps1 -Version 1.4.5
.\package-asset-generator-portable.ps1 -Version 1.4.5
```

Useful source commands:

```powershell
python tools\bazaar_skin_manager.py detect
python tools\bazaar_skin_manager.py status
python tools\bazaar_skin_manager.py doctor
python tools\bazaar_skin_manager.py --pack C:\path\to\pack validate-pack
python tools\bazaar_skin_manager.py --pack C:\path\to\pack plan-install
python tools\asset_generator_core.py --profile C:\path\to\generator-profile.json --all
```

## Release policy

GitHub releases contain the integrated manager in two distribution formats. CI packages:

- the Skin Manager per-user installer;
- the Skin Manager portable ZIP;
- SHA-256 metadata for every artifact.

No skin pack is attached to a GitHub release or embedded in the installer.
Release and verification details are in
[docs/release-runbook.md](docs/release-runbook.md).

## Legal

This is an unofficial community tool and is not affiliated with or endorsed by
Tempo or The Bazaar. Users and pack creators are responsible for ensuring that
they have the rights to any content they import or distribute.
