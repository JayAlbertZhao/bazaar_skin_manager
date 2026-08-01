# Deterministic skin-pack builder

`tools/skin_pack_builder.py` derives deployment-ready flat textures from
source artwork without calling an image-generation model or network service.
The selected adapter owns every output size, alpha envelope, anchor, native
Texture2D target, and supported original bundle hash.

The first audited recipe targets Dooley's default skin on Steam build
`24001960`. It produces only the surfaces that can be derived and deployed
faithfully as independent Texture2D assets:

- gameplay portrait;
- store and marketplace image aliases;
- collection and daily/weekly preview aliases.

Hero-select badges and small hero emblems are intentionally outside this
pipeline. Their frame/base fidelity requires direct extraction from the
original art or a separately reviewed, reference-constrained image-editing
workflow. The deterministic raster builder must not synthesize or redraw
those frames.

## Background removal

Opaque source images use edge-connected matte removal. The builder estimates
the matte from the perimeter, flood-fills only connected matte pixels, keeps
enclosed light regions such as eyes, and decontaminates antialiased edges.
Already-transparent artwork remains unchanged. This is deterministic image
processing rather than semantic segmentation.

## Example

```powershell
.\.venv-manager\Scripts\python.exe tools\skin_pack_builder.py `
  --adapter dooley-default `
  --character C:\path\to\character.jpg `
  --workspace-root .\packs `
  --output .\releases\private\dooley-example.zip `
  --pack-id local.dooley.example `
  --name "Dooley Example" `
  --version 0.1.0
```

The workspace archives the selected source under `authoring/inputs/`, but the
exported ZIP contains only `mod.json`, `asset-index.json`, and assets declared
by the manifest. The manifest records input and output hashes, alpha bounds,
coverage, target sizes, recipe version, and adapter version.

ZIP entry timestamps and permissions are normalized. Identical inputs,
metadata, adapter recipe, Pillow version, and generator version therefore
produce identical PNG and ZIP hashes.

