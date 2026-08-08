# The Bazaar game-build compatibility notes

This is the durable checklist and evidence log for adapting the Skin Manager
after a Steam update. Adapter declarations remain the executable source of
truth; this note records how their claims were established.

## Build 24570932 (2026-08-06)

### Update evidence

- Previous installed build: `24001960`.
- Steam advertised target build: `24570932` with `732079344` bytes to download.
- Starting through Steam while the game was open/launching left staging queued.
  Closing `TheBazaar.exe` allowed Steam to finish the update; the appmanifest
  then reported build and target `24570932`, downloaded bytes equal to total,
  and state flag `4`.
- Steam app id: `1617400`.

Current fail-closed runtime fingerprint:

| File | SHA-256 |
|---|---|
| `TheBazaar.exe` | `28d830d25fef1a5310a0356c851eeebcc8b255f59dd9e3f8ca742954deacf8db` |
| `TheBazaar_Data/Managed/TheBazaarRuntime.dll` | `6bcdd206c4ae44110363ec67fe45981cdbf2d2ead2ddd0d35d7d673450f8cfff` |
| `TheBazaar_Data/StreamingAssets/aa/catalog.hash` | `b23cba01cb63926b2dbccd9e4d17ccd04743a21e74796d82aa66edf24393f343` |

### Existing adapter migration

The Unity version remains `6000.3.11f1`. All seven default-skin bundles have
new SHA-256 values but retain the adapter-declared object names and dimensions.
Dooley's hero-select button bundle also changed; the other verified hero-select
and small-icon bundles retained their previous hashes.

| Hero | Default skin bundle SHA-256 on 24570932 |
|---|---|
| Mak | `6038715302da406eb79d038074ba7731890cb03d91bbb29113fe4c52a4ca32a7` |
| Vanessa | `ad389051824dd9cc642c4b6e7cc11d74095eb74c6f218c81659797844b48dd1c` |
| Pygmalien | `f38b853bb9b081217d410c23dab1e802aaecc16dbef972e68214a748de5887ab` |
| Dooley | `bbd784478e9c380cf98d81da8bb6d5c5baa3f6cda4727c26020ee694c35b1f85` |
| Jules | `56ed20907a3ab9257815b6006737afa92fdec8ffb0fdbcf7cf18ffc8b796cd1a` |
| Stelle | `f394ce09d21690ba3ea460b6ee4627ba1cb3971ff7c5bb730238e2ce8b122dd2` |
| Karnok | `58d0b6065ea4f1d3789e4fdb27e8c83352badc055707bda6788400c411d04a52` |
| Karnok creature | `0aeaeab7a074a6a0e67790ec37accf50714b1cbccbdf0b7d0052d73eff8e7649` |

The prior hashes remain declared beside the new hashes, so build `24001960`
support is not discarded.

The managed UI API renamed `HeroSelectDisplay.UpdateSelectedSkin` to
`UpdateHeroDisplay(EHero, Boolean, Action)`. The runtime resolves the old name
first and then the new name, preserving both verified builds. The diagnostic
portrait call also has to enter the same explicit local-player ownership scope
as the audit harness; otherwise its synthetic call has no `BoardBuilder`
call-stack evidence and produces a false `<null>` failure.

Build `24570932` also changed `HeroSelectDisplay._skinEditActiveSkin` from a
`GameObject`-shaped value to `SkinEdit.SkinEditComponent`. Reflection code must
normalize either representation to the component's `gameObject`. A direct
`as GameObject` cast silently returns `null`: the 0.9.63/1.0.0 PvP path still
reaches `AnimateMaterialsIn`, but exits before applying the local standing
overlay. This type-shape check belongs in the managed-API compatibility audit,
not only in image/bundle verification.

The encounter portrait pipeline also gained first-class animated portraits.
`SkinAssetDataSO.GenerateEncounterData` now copies
`animatedPortraitPrefabReference` and animation/transform metadata into the
generated `EncounterAssetDataSO`. `EncounterController.SetupEncounterController`
checks `UseAnimatedPortrait` before `HasConfiguredPortraitSprite`; when the
animated reference is valid it never calls the static sprite loader. A local
static skin-pack override must therefore replace the generated portrait and
background fields **and** clear the generated animated reference. This mutation
is performed only after the `BoardBuilder.LoadHeroPortraitAsync` ownership gate.
The opponent result retains its complete native animated/static route.
UnityPy inspection of all eight `Skin_*_01a_SkinData` objects on build
`24570932` found an empty animated portrait GUID, so this precedence change did
not cause the reported Pygmalien/Vanessa screenshots. The observed causes remain
the `SkinEditComponent` field-shape change and the later generic-scanner leak.
The generated-reference guard is still required for non-default/future skins
that populate the new field.

The same update introduced `PortraitSwapSystem`, which can temporarily swap
either combatant's portrait from combat-simulation art keys and guards its
asynchronous loads with cancellation/version state. These transient swaps are
not default-skin ownership evidence: their `SkinAssetDataSO` calls intentionally
fail closed as `owner=unknown`. Resetting the swap restores the already-routed
default portrait captured from the board. The manager does not patch the swap
system or its per-combatant controller state.

Multi-pack deployment introduces a separate ownership boundary. The generic
visible-UI scanner may route safe collection and store surfaces across every
enabled pack, but it must never route `portrait_gameplay`, `portrait_small`, or
`portrait_background`. The same native portrait name is present on local and
opponent `EncounterController` objects, and the opponent object does not have a
stable `Opponent`-named transform ancestor. Gameplay portrait slots belong only
to the owner-aware loader path. In the observed failure the
`GenerateEncounterData` hook correctly logged `wrong owner` and retained the
opponent's native references, after which the generic scanner overwrote the
already-created opponent image. Filtering these slots at the scanner boundary
preserves the local loader replacement and prevents that second-stage leak.

### Runtime and reversal evidence

The final `1.2.0` runtime was deployed to `Hero8 / Skin_DRA_01/A` with the
existing Kotone visual pack as a cross-profession test payload. A Steam launch
loaded the runtime without Harmony binding warnings, found
`Skin_DRA_01a_SkinData`, routed `LoadPortraitSpriteAsync` through the exact
local-player replacement, and resolved `BazaarSkinManager/portrait_gameplay`
at `1024x1024`. The runtime self-test passed on build `24570932`.

The same deployment patched all three declared DRA bundles and their catalog
CRCs. Undeploy restored the original catalog and these exact bundle hashes:

- skin bundle: `a09837c424b3412706f888f4f70efceeab9bbfdd8ef70f6cc9a7753a6f541748`;
- small icon bundle: `0f7e227a68c9f800a7afa384258df808683f9e9b5d33cee0187fe1a04c22e1d3`;
- hero-select bundle: `5a569924996c4f893aa9c5d867e1affe9b8a9e73cc6bdbd8bd817ddf1f05f52f`.

Startup-only audit calls can legitimately return `null` for addressable
surfaces whose owning screen has not loaded. They are recorded as `not loaded`,
not treated as proof of a broken adapter. Adapter authorization remains based
on the installed build id, exact bundle hash, exact Texture2D name and exact
dimensions; active runtime routes are verified separately through the game log.

### New hero: The Dragons

The game's managed enum exposes the eighth hero as `Hero8`; the Hero Scriptable
Object supplies the display name **The Dragons**, description names Rin and Jin,
and asset code `DRA`. Runtime routing must therefore use `Hero8` as the manifest
hero and `DRA_01a` as the skin-name token. Using only the marketing name would
miss `HeroID.ToString()` in the generic runtime matcher.

Verified default-skin contract:

- target: `Hero8 / Skin_DRA_01/A`;
- skin bundle: `skin_dra_01_assets_all.bundle`;
- store texture: `Skin_DRA_01a_StoreImage_TUI`, `2048x2048`;
- collection texture: `Skin_DRA_01a_PreviewCollection_TUI`, `512x512`;
- portrait texture: `Skin_DRA_01a_Portrait`, `1024x1024`;
- hero-select texture: `TheDragons`, `512x512`, bundle SHA-256
  `5a569924996c4f893aa9c5d867e1affe9b8a9e73cc6bdbd8bd817ddf1f05f52f`;
- small icon: `Icon_FlatRough_DRA_TUI`, `256x256`, bundle SHA-256
  `0f7e227a68c9f800a7afa384258df808683f9e9b5d33cee0187fe1a04c22e1d3`;
- default skin bundle SHA-256:
  `a09837c424b3412706f888f4f70efceeab9bbfdd8ef70f6cc9a7753a6f541748`.

### Repeatable compatibility procedure

1. Read `appmanifest_1617400.acf` before updating; record installed and target
   builds. Trigger the update through Steam and ensure the game is closed while
   Steam stages files.
2. Fingerprint the executable, managed runtime, and Addressables catalog hash.
3. Enumerate new hero Scriptable Objects and managed hero enum values. Treat
   the enum string used at runtime separately from the display name.
4. For every adapter deployment, verify the target bundle exists, its SHA-256
   is authorized, and the declared Texture2D name and size still exist exactly.
5. Add a new build id and hash without deleting an older verified build unless
   that support is intentionally retired.
6. Keep direct runtime matching generic: exact hero id or asset code, exact skin
   token, local-player ownership checks, and original-asset fallback.
7. Re-run registry, pack validation, native patch, runtime structure, full test,
   frozen UI, deploy/undeploy, and game-log checks before claiming support.
8. Re-audit `IPortraitAssetData` precedence and all callers of
   `LoadPortrait`, `LoadPortraitSpriteAsync`, and `GenerateEncounterData`;
   field-compatible code can still be bypassed by a newly preferred visual
   route such as animated portraits.

Unknown builds or hashes must stay blocked. A familiar filename is evidence to
investigate, not permission to patch it.
