# All-hero default-skin runtime verification

Release verification for Manager, runtime, and asset generator `1.0.0` on
Steam build `24001960`.

## Method

Each default-skin adapter was generated and deployed independently through the
asset generator and the Manager's reversible deployment API. The game was
started through Steam for every target. A request-gated runtime audit exercised
the exact target `SkinAssetDataSO`, safe loader surface, central hero-selection
placeholder, repeated attachment, and restoration to the original state.

The audit's local-portrait scope exists only while invoking
`LoadPortraitSpriteAsync` from the explicit diagnostic request. Normal runtime
ownership checks remain unchanged. The request file was removed after the run.

## Results

| Hero | Target | Gameplay portrait | Central standing | SkinEdit placement | Inspector background |
| --- | --- | --- | --- | --- | --- |
| Dooley | `Skin_DOO_01a_SkinData` | applied | applied and restored | applied | applied |
| Mak | `Skin_MAK_01a_SkinData` | applied | applied and restored | applied | applied |
| Vanessa | `Skin_VAN_01a_SkinData` | applied | applied and restored | applied | applied |
| Pygmalien | `Skin_PYG_01a_SkinData` | applied | applied and restored | applied | applied |
| Jules | `Skin_JUL_01a_SkinData` | applied | applied and restored | applied | applied |

Vanessa and Pygmalien were also switched to their default skins through the
normal collection UI and visibly rendered the generated central artwork.
Dooley and Mak retain the previously verified normal-selection behavior.
Jules is purchase-locked on the test account; its real loaded default skin and
live `HeroSelect` placeholder were therefore exercised through the bounded,
reversible diagnostic instead of initiating a purchase.

The central diagnostic result for every hero reported:

```text
targetApplied=True targetAlreadyApplied=True nonTargetStateRestored=True
```

## Raw audit fingerprints

The temporary raw reports were checked before cleanup:

- Dooley: `62e5bb506e42192983259e84b3d666e8dc994f5150779cb7707bff19ed61d13b`
- Jules: `3ecbf898ab6761680a51f0362070ac5424ba95a0eecd71d618938dab8299a3df`
- Mak: `2ee8708356a8e8c33edf77aebbbc931954b44f582c07b5ad511e97384d3a7d13`
- Pygmalien: `2d8f8e8509798f4b2a575c52318c95677f9e21bd3105eb822d22ebd51633712a`
- Vanessa: `1487456d9a81ef7faa2b4a33a72e9cbe5079bae4d5e657164816a37b6447f730`

The final automated suite contains 113 passing tests.
