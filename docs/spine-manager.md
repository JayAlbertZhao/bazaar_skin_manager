# Bazaar Spine Manager

`bazaar_spine_manager.exe` imports a compatible external Spine package and
rebuilds a verified The Bazaar hero-skin bundle while preserving the original
Addressables paths, prefab references, object IDs, and bundle filename.

## Supported package

The importer supports this deterministic contract:

- one ZIP or extracted directory;
- one Spine 4.1 or 4.2 JSON file;
- one `.atlas` file;
- one or more PNG pages declared by that atlas;
- a `default` skin;
- at least one animation.

PNG files below source folders such as `images/` are ignored unless the atlas
declares them as pages. Multi-page atlases are merged vertically into one
runtime texture and every region coordinate is translated to the merged page,
because the verified Bazaar target bundle contains one Texture2D and one atlas
material. Packages exported by Spine 4.1 have their JSON runtime version marker
normalized to the game's Spine 4.2 runtime during deployment.

The selected animation is copied to the game's expected `idle` animation.
The tool rewrites the atlas page to the target Unity texture name, converts
the texture to premultiplied alpha, calculates a scale from the original and
replacement Spine/atlas dimensions, and applies user X/Y/scale adjustments.

## Backup and restore

Original bundle and `catalog.bin` files use the Skin Manager's verified native
backup store below:

```text
%LOCALAPPDATA%\BazaarSkinManager\TheBazaar\native-backups
```

Every later deployment is regenerated from those verified originals rather
than from a previously modified bundle. Spine changes and raster Texture2D
changes targeting the same bundle are composed before the Manager atomically
updates that bundle and the single Addressables catalog. Removing Spine
replacements preserves other enabled skin packs.

## Preview

The placement tab contains an offline Tk canvas preview. It renders a static
setup pose from the imported Spine JSON, meshes, weights, atlas, and source
images. Dragging the character updates the deployment root X/Y values directly.
The background is extracted from the game's Hero Select scene, and the Ready UI
occlusion layer uses the scene's 3840×2160 reference coordinates. Preview does
not start a browser, require a screenshot upload, or require internet access.

## Diagnostic logs

The application writes rotating UTF-8 diagnostic logs to:

```text
%LOCALAPPDATA%\BazaarSkinManager\TheBazaar\spine-manager\logs\bazaar_spine_manager.log
```

Use **Open log directory** in the main window to locate it. Deployment records
every stage, elapsed time, a heartbeat every 15 seconds during long-running
stages, and complete exception tracebacks. Logs rotate at 5 MB with five older
files retained.

## Build

On Windows with Python 3.12 and `manager\requirements-build.txt` installed:

```powershell
.\build-spine-manager.ps1 -Version 1.2.0
```

The executable is written to:

```text
dist\spine-manager\bazaar_spine_manager.exe
```
