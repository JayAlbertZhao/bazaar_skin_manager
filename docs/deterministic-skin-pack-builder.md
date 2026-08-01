# Deterministic skin-pack builder

`tools/skin_pack_builder.py` derives deployment-ready flat textures from
three explicit inputs without calling an image-generation model or network
service:

- a character image;
- a background image;
- a small icon, which may itself be prepared by the included deterministic
  binary-alpha crop.

The selected adapter owns every output size, alpha envelope, anchor, native
Texture2D target, and supported original bundle hash.

The first audited recipe targets Dooley's default skin on Steam build
`24001960`. It produces only the surfaces that can be derived and deployed
faithfully as independent Texture2D assets:

- gameplay portrait;
- store and marketplace image aliases;
- collection and daily/weekly preview aliases;
- the independent 256x256 hero icon.

The hero-select badge remains outside this recipe. Its frame/base must be
extracted from original game art; the deterministic raster builder must not
synthesize or redraw it.

Each output recipe declares `depends_on` and an ordered `layers` list. The
current composites use background-cover followed by alpha-contained character
art. The small hero icon depends only on the supplied small-icon input.

## Background removal

Opaque source images use edge-connected matte removal. The builder estimates
the matte from the perimeter, flood-fills only connected matte pixels, keeps
enclosed light regions such as eyes, and decontaminates antialiased edges.
Already-transparent artwork remains unchanged. This is deterministic image
processing rather than semantic segmentation.

For a source image without a separate icon, `--derive-small-icon-output`
applies the same matte removal, limits processing to a caller-supplied
normalized rectangle, thresholds alpha to a binary mask, and crops to that
mask's bounding box. This is an explicit geometric prior, not object
recognition.

## Example

```powershell
.\.venv-manager\Scripts\python.exe tools\skin_pack_builder.py `
  --adapter dooley-default `
  --character C:\path\to\character.jpg `
  --background C:\path\to\background.png `
  --derive-small-icon-output C:\path\to\small-icon.png `
  --small-icon-region 0.15 0.12 0.98 0.59 `
  --input-metadata C:\path\to\input-metadata.json `
  --workspace-root .\packs `
  --output .\releases\private\dooley-example.zip `
  --pack-id local.dooley.example `
  --name "Dooley Example" `
  --version 0.1.0
```

The workspace archives all three selected inputs under `authoring/inputs/`,
but the exported ZIP contains only `mod.json`, `asset-index.json`, and assets
declared by the manifest. The manifest records each input's origin, AIGC
declaration, license/source metadata where applicable, hashes, output
dependencies, layer order, alpha metrics, target sizes, recipe version, and
adapter version. A metadata entry declaring `aigc: true` is rejected.

ZIP entry timestamps and permissions are normalized. Identical inputs,
metadata, adapter recipe, Pillow version, and generator version therefore
produce identical PNG and ZIP hashes.
