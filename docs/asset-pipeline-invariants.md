# Asset-pipeline invariants

These are hard project constraints, not optional implementation preferences.

## Faithful extraction uses alpha masks, never colour inference

When an official asset must be separated into layers, the extraction mask must
come from one of these sources:

1. an explicitly authored alpha/lasso mask;
2. geometry authored independently of the source image's RGB values;
3. a separately approved high-fidelity image edit, with its provenance and
   AIGC status declared.

Code must **not** infer a faithful extraction mask from RGB/HSV values,
luminance, colour distance, "gold"/"white"/"dark" thresholds, or similar-pixel
classification. Those methods remove valid material highlights and other
colours that belong to the asset. They are prohibited for badge bases, badge
frames, portraits, and other fidelity-critical source layers.

For several already-aligned official variants of the same template, exact
cross-asset pixel equality may be used to locate the shared template region.
This comparison must be colour-agnostic: every repeated RGBA value, including
white highlights and dark shadows, is treated equally. Use the maximum
connected common region; do not compare pixels to a reference colour and do
not introduce a colour-distance tolerance.

For the official hero-select frame, the approved deterministic extraction path is:

- read pixels from an original installed-game texture;
- derive the frame Alpha from the maximum connected exact-common region across
  the official profession badges, then recover disconnected exterior fringe
  pixels from the exact-common set using only proximity to that region and the
  authored base boundary; explicit lasso geometry is allowed for
  template-specific completion;
- preserve the selected RGB pixels without repainting or colour filtering;
- split the result at the two lower corners into `frame_upper` and
  `frame_lower` by alpha masks;
- derive a lower-layer knockout mask from the frame geometry so the visually
  transparent area below the lower border erases character pixels;
- compose back-to-front as
  `base -> frame_upper -> clipped character -> frame_lower`.

When the pack author explicitly permits AIGC for the badge substrate, an
approved completion path may replace the incomplete deterministic fill:

- first derive and preserve the exact aligned partial official frame/wood
  overlap;
- use that overlap as the ImageGen edit target and record the prompt, source,
  model-path provenance, and resulting hash;
- register the completed blank badge against the extracted partial official
  frame by Alpha-mask overlap only; never optimize on RGB or material colour;
- derive the upper/lower frame partition from the completed asset's Alpha
  geometry, with an authored frame width and corner split;
- keep the completed substrate as the full backing so wood reaches beneath the
  rim, contact shadow remains continuous, and transparent seams cannot appear.

If a character source contains a cast shadow or other source underlay that
belongs to the background, split it with an explicit authored-coordinate lasso.
Background composites place it between the background and character; the badge
merges it into `base` before compositing `frame_upper`. The lasso is geometry;
it must never be inferred from whether a source pixel looks black or dark.

Once a user has approved a high-fidelity edited cutout, that cutout becomes
the canonical character input. Later deterministic builds must not silently
fall back to the original opaque JPEG and rerun generic matte removal: doing
so restores enclosed background holes and invalidates every authored shadow
lasso. Keep the original, edit intermediate, final Alpha cutout, and edit
provenance as separate files.

If no adequate alpha/lasso mask exists, the tool must fail clearly. It must not
silently fall back to colour similarity or generate a code-drawn imitation.

For a pack that declares `aigc_allowed: false`, an ImageGen-derived template is
also forbidden. Such a pack must use the original-texture-plus-alpha-mask path.
