# Portrait and SkinEdit standing ownership

This note defines the runtime ownership boundary for match portraits and
SkinEdit standing presentations. It is intentionally stricter than asset-name
matching: a skin name identifies the asset pack, not the player who owns the
visible object.

## Evidence and historical constraints

- In `0.4.2`, `standing_overlay` was attached while
  `LoadSkinEditSkinAsync` still returned an unparented/inactive prefab. Its
  renderer bounds and layer were not final, which caused visible replacement
  failures.
- `0.4.3` moved the exact placement replacement to the postfix of
  `HeroSelectDisplay.AnimateMaterialsIn`. At that point the game had parented,
  layered and activated the exact placement.
- `0.4.4` added a bounded visible-frame attacher for non-PvP placements whose
  renderer bounds become valid one or more frames later. `0.4.5` through
  `0.4.7` established the camera-projection, billboard, mirror compensation,
  vertical offset and child-lifetime behavior still used by the runtime.
- `0.9.62` introduced the local `BoardBuilder+<LoadHeroPortraitAsync>` gate and
  exact `HeroSelectDisplay -> HeroViewTransition._selectedHero -> _isHero`
  ownership check. `0.9.63` and `1.0.0` retain that design and are the stable
  reference versions.
- Steam build `24570932` changed
  `HeroSelectDisplay._skinEditActiveSkin` from a `GameObject`-shaped field to a
  `SkinEditComponent`. The component's `gameObject` is the same owned root;
  treating the field itself as `GameObject` silently returns `null` and skips
  the otherwise-correct visible-time path.
- The same build made the generated `EncounterAssetDataSO` prefer
  `animatedPortraitPrefabReference` over `portraitTextureReference`. For a
  proven local owner, the generated animated reference must be cleared along
  with replacing the static portrait/background fields; otherwise the game
  never reads the replacement sprite. Opponent encounter data is not mutated.
- The 2026-08-07 game log proves that the opponent
  `GenerateEncounterData` call was already rejected as `wrong owner`, yet the
  opponent portrait was still replaced. The later generic UI scan was the
  remaining replacement route. Multi-pack loading made that leak reproducible
  because a pack matching the opponent profession was now available.
- The 2026-08-08 game log proves the inverse route worked correctly for the
  local Pygmalien (`owner=local`) but was still retained as `unsupported type`.
  That lightweight pack contains `portrait_gameplay` but intentionally omits
  `portrait_background`; the encounter mutator incorrectly required both
  fields/assets as one atomic replacement. Encounter slots are now applied
  independently, so an omitted optional background retains the native
  background without suppressing the configured foreground portrait.

## Authoritative routing matrix

| Surface | Ownership proof | Replacement point | Unknown-owner behavior |
|---|---|---|---|
| Local board portrait foreground/background | Call stack contains `BoardBuilder+<LoadHeroPortraitAsync>` | Independently replace each configured `GenerateEncounterData` result field; `LoadPortraitSpriteAsync` where used | Retain native |
| Opponent board portrait | Opponent setup path or absence of the local `BoardBuilder` proof | None | Retain native |
| Store, collection, battle-pass and career portrait previews | Explicit preview call-site whitelist | `SkinAssetDataSO.LoadPortrait` postfix | Retain native |
| Generic `Image`/`RawImage`/`SpriteRenderer` scan | No reliable owner proof exists | Never for `portrait_gameplay`, `portrait_small`, or `portrait_background` | Skip |
| PvP standing presentation | Exact `HeroSelectDisplay` is referenced by one `HeroViewTransition._selectedHero`; `_isHero` is true | After `HeroSelectDisplay.AnimateMaterialsIn` | Retain native |
| Opponent PvP standing presentation | Same exact display relationship; `_isHero` is false | None | Retain native |
| End-of-day, start-of-day and other non-PvP SkinEdit placements | The screen itself is a local-only placement | Visible-time postfix, with bounded visible-frame fallback | Retain native on timeout |

## SkinEdit lifecycle

1. `LoadSkinEditSkinAsync(placement)` instantiates the full SkinEdit prefab and
   selects one exact placement.
2. For `PvpScreen`, the loader postfix does not attach anything because the
   API has no player/opponent parameter.
3. `HeroSelectDisplay.CreateNewSkin` obtains the `SkinEditComponent`, parents
   it below `skinEditContainer`, applies the final layer, and initially
   deactivates it.
4. `AnimateMaterialsIn` fades and activates the component. Its postfix
   normalizes either the old `GameObject` or new `Component` field shape to the
   same root.
5. For `PvpScreen`, the postfix resolves the exact owning
   `HeroViewTransition`. Only `_isHero == true` may attach the overlay.
6. The overlay is parented below the exact placement renderer. It inherits the
   transition's position, layer, vortex occlusion and destruction lifecycle;
   it must not be detached into a screen-space/global root.
7. Non-PvP placements may also carry a 120-frame bounded attacher. It waits for
   an active renderer with non-zero bounds and disables itself after attach or
   timeout.

## Portrait lifecycle

The local board calls `BoardBuilder.LoadHeroPortraitAsync`, which calls
`SkinAssetDataSO.GenerateEncounterData`, assigns the returned
`portraitTextureReference` and `backgroundTextureReference` to an
`EncounterController`, parents the controller, and activates it. The opponent
path independently obtains the opponent's equipped skin and also calls
`GenerateEncounterData`. Therefore the `SkinAssetDataSO` and its asset names do
not establish ownership; the caller does.

The generated encounter's foreground and background are independent optional
slots. A pack that configures only `portrait_gameplay` replaces only
`portraitTextureReference`; `backgroundTextureReference` remains native. The
animated portrait reference is cleared only when a foreground portrait is
actually configured, because that native route otherwise takes precedence
over the generated static sprite.

`LoadPortrait(bool)` is a different API used by non-match preview surfaces in
build `24570932`. It is allowed only from the explicitly verified preview
callers. Any new caller fails closed until its role is classified. The generic
scanner remains useful for store and collection assets, but all three portrait
slots are excluded before it can consult any enabled pack.

Combat-time `PortraitSwapSystem` routes are separate from the default board
portrait. They carry an explicit `ECombatantId`, cancellation token and request
generation internally, but that ownership is not present at the lower
`SkinAssetDataSO` loader hook. Those calls fail closed and retain their native
temporary art. When the swap resets, the system restores the default sprite it
captured from the already-routed board portrait.

Every owner-aware portrait decision emits one deduplicated line containing
`slot`, `pack`, `skin`, `owner`, `callSite`, and `action=applied|retained`.
These lines are the acceptance evidence; an opponent/unknown line with
`action=applied` is a runtime defect.

## Acceptance matrix

After the log confirms the intended runtime version is loaded:

1. Local Pygmalien versus opponent Vanessa:
   - local PvP standing is replaced;
   - opponent PvP standing remains native;
   - local bottom board portrait is replaced;
   - the background is replaced only when the local pack configures
     `portrait_background`; otherwise it remains native;
   - opponent top board portrait remains native.
2. Start-of-day and end-of-day:
   - local standing is replaced only after the exact placement becomes
     visible;
   - vortex occlusion, camera framing and vertical offset remain correct;
   - the overlay is destroyed with the transition and does not persist onto
     the board.
3. Repeat with another profession pair while all packs remain enabled.
4. Verify store/collection portrait previews still replace through a logged
   `owner=preview` route.
5. Disable deployment and confirm native assets and catalog/bundle hashes are
   restored.

Offline structure tests and a successful build validate the routing contract,
but do not replace this in-game matrix.

After completing the matrix, run the log verifier against the same game
session. It exits `0` only when every required local/opponent route is present,
`1` while evidence is incomplete, and `2` for an unsafe apply or mounting
failure marker:

```powershell
.\.venv-manager\Scripts\python.exe tools\verify_portrait_routing_log.py `
  'D:\SteamLibrary\steamapps\common\The Bazaar\BepInEx\LogOutput.log' `
  --runtime-version 1.3.2
```
