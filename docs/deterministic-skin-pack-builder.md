# Deterministic skin-pack builder

The project-wide extraction constraints in
[`asset-pipeline-invariants.md`](asset-pipeline-invariants.md) are normative.
In particular, fidelity-critical masks may not be inferred from pixel colour
or colour similarity.

`tools/skin_pack_builder.py` derives deployment-ready flat textures from
explicit inputs without calling an image-generation model or network service.
Every recipe declares which inputs are required or optional:

- a character image;
- a background image;
- a small, single-colour icon, which may itself be prepared by the included
  deterministic binary-alpha crop and stencil conversion.

The shared recipe additionally accepts an optional transparent `standing_prop`
for the central standing presentation. Its adapter-declared transform and layer
order place it against the character without leaking into portraits, badges,
icons, or store images. When omitted,
the standing overlay retains the original character-only dependency graph.

The selected adapter owns every output size, alpha envelope, anchor, native
Texture2D target, and supported original bundle hash.

The audited adapters target the default skins of Mak, Vanessa, Pygmalien,
Dooley, and Jules on Steam build `24001960`. They share the complete raster
recipe and override only verified native differences such as hero-button size.
The recipe produces the surfaces that can be derived and deployed faithfully
as independent Texture2D assets:

- gameplay portrait;
- store and marketplace image aliases;
- collection and daily/weekly preview aliases;
- standing and collection-detail surfaces;
- a layered hero-select badge at the target hero's native 256x256 or 512x512 size;
- the independent 256x256 hero icon.

The hero-select badge begins with a local template extracted from the installed
game's official hero-button textures. The extractor overlays the five aligned
profession badges and preserves the partial frame/wood region whose RGBA pixels
recur exactly. When the pack author permits it, that real overlap may be used as
an ImageGen edit target to reconstruct the character-occluded empty substrate.
The result is then registered back to the extracted partial frame by Alpha-mask
Dice overlap and split geometrically; RGB/HSV thresholds, luminance tests,
reference colours, and colour-distance tolerances remain forbidden. Every
source, registration value, and output hash is recorded in `template.json`.

Prepare that local dependency once (and again after an incompatible game-art
update):

```powershell
.\.venv-manager\Scripts\python.exe tools\badge_pipeline.py extract-game-template `
  --game-dir "D:\SteamLibrary\steamapps\common\The Bazaar" `
  --output-dir .\manager\assets\badge-templates\hero-select-gold
```

After an explicitly approved ImageGen completion, build the production layers
and register them against the preserved partial official frame mask:

```powershell
.\.venv-manager\Scripts\python.exe tools\badge_pipeline.py build-completed-base-template `
  --completed-base .\manager\assets\badge-templates\hero-select-gold\imagegen\base_completion_v1_512.png `
  --official-frame-mask .\manager\assets\badge-templates\hero-select-gold\imagegen\official_partial_frame_mask.png `
  --output-dir .\manager\assets\badge-templates\hero-select-gold `
  --split-y 350 `
  --frame-width 20
```

The compositor's back-to-front layer contract is fixed and audited:
`base(+optional authored cast-shadow underlay) -> frame_upper -> clipped
character -> frame_lower`. The lower layer begins at the two lower corners;
its geometric transparent knockout removes every character pixel below the
border before the visible frame is composited. This places a lasso-separated cast shadow from the source scene
behind the frame, keeps the character in front of the upper frame, and places
only the minimal lower tip over the character. The cast-shadow lasso is authored
geometry; the builder never identifies it by checking for black or dark pixels.

Each output recipe declares `depends_on` and an ordered `layers` list. A source
cast shadow is a deterministic derivative of `character`; background composites
use `background -> character_shadow -> character`, while a transparent standing
overlay omits the shadow. The small hero icon depends only on the supplied
small-icon input.

## Background removal

Opaque source images use edge-connected matte removal. The builder estimates
the matte from the perimeter, flood-fills only connected matte pixels, keeps
enclosed light regions such as eyes, and decontaminates antialiased edges.
Already-transparent artwork remains unchanged. This is deterministic image
processing rather than semantic segmentation.

For a pack-author-approved composite source, set `character.authoritative_alpha`
to `true` in the input metadata. The builder then uses its RGBA pixels verbatim:
it performs no matte removal and does not apply legacy transparency or cast-shadow
lassos. This is the correct mode when several manually prepared elements have
already been flattened into the canonical character source. The manifest records
the bypass and the source hash so the full build can be audited and reproduced.

For a source image without a separate icon, `--derive-small-icon-output`
applies the same matte removal, limits processing to a caller-supplied
normalized rectangle, thresholds alpha to a binary mask, and crops to that
mask's bounding box. The selected `--small-icon-preset` then applies one of
three deterministic conversions:

- `outline` (default): flatten colours, map coloured regions to white, and map
  near-black ink to transparent gaps. Best for cartoons with explicit ink;
- `block-gaps`: flatten and merge palette regions, map every region to white,
  and geometrically inset adjacent regions to create transparent gaps. It does
  not assume any particular outline colour;
- `silhouette`: map the complete alpha silhouette to solid white.

All three preserve the outer silhouette and use explicit raster geometry
rather than object recognition.

## Example

```powershell
.\.venv-manager\Scripts\python.exe tools\skin_pack_builder.py `
  --adapter dooley-default `
  --character C:\path\to\character.jpg `
  --background C:\path\to\background.png `
  --derive-small-icon-output C:\path\to\small-icon.png `
  --small-icon-region 0.20 0.07 0.94 0.76 `
  --small-icon-preset outline `
  --supplemental-input standing_prop=C:\path\to\transparent-prop.png `
  --input-metadata C:\path\to\input-metadata.json `
  --badge-template-root .\manager\assets `
  --workspace-root .\packs `
  --output .\releases\private\dooley-example.zip `
  --pack-id local.dooley.example `
  --name "Dooley Example" `
  --version 0.1.0
```

The workspace archives all supplied inputs under `authoring/inputs/`,
but the exported ZIP contains only `mod.json`, `asset-index.json`, and assets
declared by the manifest. The manifest records each input's origin, AIGC
declaration, license/source metadata where applicable, hashes, output
dependencies, layer order, alpha metrics, target sizes, recipe version, and
adapter version. A metadata entry declaring `aigc: true` is rejected.

ZIP entry timestamps and permissions are normalized. Identical inputs,
metadata, adapter recipe, Pillow version, and generator version therefore
produce identical PNG and ZIP hashes.

## End-to-end generator UI

`tools/asset_generator_ui.py` is intentionally separate from the Skin Manager
UI. The generator owns only authoring and packaging; ZIP import, reversible
deployment, backup restoration, compatibility checks, and doctor diagnostics
continue to use `StudioWorkspace` and the existing Manager implementation.

Launch it from a source checkout with:

```powershell
.\launch-asset-generator.ps1
```

A generator project accepts up to three author materials:

- one required authoritative RGBA character composition;
- one optional background image;
- one optional single-colour small icon.

Small-icon derivation may use a fourth authoring-only source image. It is
independent of the character composition, appears in its own preview card, and
is archived as provenance. When omitted, derivation falls back to the character
source for backward compatibility.

The authoring UI reuses Skin Manager's explicit colour-screen operation for
imports and clipboard images. Authors may select green, white, or a custom
`#RRGGBB` key plus tolerance; the processed result is saved as a new project PNG
and never overwrites the selected source file. Every material row also exposes
an independent clear action. Clearing removes the project selection and restores
transparent/original fallback behavior without deleting the author's source.

It also declares the audited adapter, input metadata, local badge template,
isolated generation workspace, output ZIP, and optional game directory. The UI
offers the same four operations as the command-line orchestrator: generate,
import, deploy, and run the full sequence. A clean build deletes only
`<workspace_root>/<pack_id>`; source inputs must live outside that directory.

The three image fields accept file browsing, Windows file drag-and-drop, a
copied file path, or a bitmap pasted directly from the clipboard. Clipboard
bitmaps are materialized as PNG files beside the generator profile so the next
build remains reproducible. Authoring previews are progressive: after the
character arrives, every derivable surface renders immediately; absent
background and small-icon layers remain transparent instead of blocking all
preview cards. The small icon defaults to an author-supplied file, while the UI
also exposes the deterministic `outline`, `block-gaps`, and `silhouette`
derivation presets and records the selected preset in input provenance.
Generation is progressive as well: if an optional author material is still
absent, the ZIP omits only the adapter outputs that depend on it. It never
installs a fully transparent replacement over the game's original texture.
The character placement canvas supports direct
mouse panning and reports integer source-canvas X/Y offsets. Changing this
global position live-renders every output that contains the character; it does
not move backgrounds, badge frames, or the separately supplied small icon.
Each generated preview is also draggable. Its integer `output_adjustments`
offset moves only the adjustable foreground inside that one output (character
and traced shadow, or the small-icon layer for the icon slot). Slots that are
aliases of one native game texture form one generated-asset family: an offset
made through any displayed alias is canonicalized to that shared asset, because
the Manager correctly rejects conflicting pixels for one native target.

Both adjustment levels are stored in the generator profile. The builder maps
the global source-canvas delta through each recipe's actual alpha-contain scale,
then applies the per-output delta in that asset's native output pixels. Non-zero
adjustments and their effective output-space values are recorded in the pack's
authoring provenance. A zero-adjustment profile stays byte-compatible with the
previous deterministic pack output.

For an unattended local run:

```powershell
.\.venv-manager\Scripts\python.exe tools\asset_generator_core.py `
  --profile C:\path\to\generator-profile.json `
  --all
```

After importing a complete pack, the generator records that Manager workspace
as the current workspace. Opening Skin Manager therefore displays the exact
pack that was generated and deployed. Private generator projects, raster
inputs, and generated ZIPs remain ignored by Git and are not part of software
releases.
