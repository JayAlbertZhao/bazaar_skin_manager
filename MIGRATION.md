# Project migration

This workspace is the independent continuation of the skin-manager subproject
previously developed inside a broader workspace.

## Imported baseline

- Source history: local source repository plus its `main`, `experimental`, and
  release tags.
- Working branch: `codex/skin-pack-builder-compat` at the latest
  `v0.9.5-experimental` baseline (`c3b4073`). This includes randomized PvP
  result-voice selection. Stable `main` remains at `v0.9.3` (`82fef1e`).
- Public remote: `https://github.com/JayAlbertZhao/bazaar_skin_manager.git`.
- Original source checkout was retained unchanged as a rollback source.

## Preserved local material

The following project-specific directories were copied intact. They remain
ignored by Git because they contain private inputs, generated evidence, or
large local artifacts:

- `.agents/`: historical planning, progress, review, and acceptance records.
- `.codex-work/`: session work products, logs, screenshots, verification
  packages, and reverse-engineering evidence.
- `animation/`, `assets/`: approved source artwork and derivation metadata.
- `handoff/`: historical handoffs plus the final conversation handoff.
- `packs/`: the working private skin/audio pack.
- `research/`: asset, audio-routing, loader, and presentation investigations.
- `releases/private/0.9.3/`: the accepted user-facing package and installer,
  including their checksum files and shortcuts.
- `releases/private/0.9.5-experimental/`: the latest portable manager and
  installer copied from the original checkout's release output.

Do not remove these ignore rules or publish those directories without a
separate privacy and redistribution review.

Copy verification at migration time:

| Directory | Files | Bytes |
| --- | ---: | ---: |
| `.agents/` | 6 | 107,409 |
| `.codex-work/` | 2,704 | 649,903,254 |
| `animation/` | 8 | 7,800,136 |
| `assets/` | 7 | 8,240,128 |
| `handoff/` | 20 | 26,047,339 |
| `packs/` | 130 | 42,964,310 |
| `research/` | 203 | 62,718,629 |
| `releases/private/0.9.3/` | 6 | 63,484,984 |
| `releases/private/0.9.5-experimental/` | 3 | 53,541,381 |

The source/target file counts and byte totals matched for every directory
copied from the old checkout. `handoff/` has one additional file: the final
external conversation handoff.

## Deliberately not copied

- `.conda/` and `.venv-manager/`: machine-specific environments; recreate an
  isolated environment when dependencies change.
- `.pytest_cache/`, `__pycache__/`, `bin/`, and `obj/`: disposable caches and
  compiler output.
- `dist/`: historical and duplicate build output. The accepted 0.9.3 artifacts
  and the latest 0.9.5 experimental manager artifacts were preserved
  separately under `releases/private/`.
- `tmp/`: disposable intermediate files.
- The raw Codex conversation log: it is almost 2 GB and remains in Codex's
  session store. The concise final handoff is preserved under `handoff/`.

## Continuation target

The original conversation explicitly assigned two next deliverables, recorded
in `handoff/HANDOFF_NEXT_CHAT_2026-08-01.md`:

1. Build a deterministic skin-pack maker. Its required inputs are one
   transparent character illustration, one background image, and one small
   portrait. It must derive adapter-defined slots, preview and validate them,
   and export a complete ZIP that the manager can import.
2. Complete multi-hero and multi-skin support: detect the replaceable skins
   actually installed for each hero, show hero and skin lists, preview original
   and replacement assets, remove default-skin hard-coding, and add verified
   adapters for other heroes.

The shared prerequisite for both deliverables is an adapter registry keyed by
hero and skin. Unsupported adapters or game hashes must remain fail-closed.

## Migration validation

- Runtime rebuilt locally with
  `build.ps1 -Configuration Release -Version 0.9.5`.
- All 67 unit tests passed after the rebuild.
- Rebuilt runtime SHA-256:
  `2235534d8a849162859edc2b0f456365aac8987a28d07998bd700e3a64d18269`.
- Preserved release checksums matched their shipped checksum files:
  - skin pack: `8ed48e441877c71f0fdc6967552bcc93f0f075a9176f9c3252c5dd0d96907df0`;
  - stable manager installer:
    `1544f48190ae4f12ce8c9294c43e398ed04eac82099b468f219c52fc294b62bb`;
  - 0.9.5 experimental manager installer:
    `6b6c25fe809adafc721e3aeee6510450241cd4324a502aaa30de9901073fa0c8`;
  - 0.9.5 experimental portable manager:
    `8d996ce9b1a18698e4b5a7c5d39a92186379774a351bbdb243ac7fd35f0db987`.

## Adapter registry and installed skin discovery

The remaining multi-hero/multi-skin foundation is now implemented without
claiming unverified runtime support. The manager scans every adapter JSON,
indexes it by `(hero, skin)`, reads exact skin identifiers from the installed
Addressables catalog, and reports `supported`, `detected_unmapped`, or
`game_update_required` per skin. Export and deployment fail closed unless the
selected skin has an adapter verified for the detected Steam build.

Verified Texture2D slots expose a read-only original/replacement comparison.
Only Mak's default adapter is currently verified; the other installed skins
are discovery results, not fabricated deployment support.

## 0.9.6 stable release validation

The PvP-entry regression was traced to the periodic central
`HeroSelectStandingState` reconciler cleaning up non-central `PvpScreen` and
`EndOfDayScreen` overlays after their exact placement patches had attached
them. Version 0.9.6 restores the ownership guard before that cleanup and adds
a regression assertion that preserves the ordering.

The continuation work and fix were validated together as a stable release:

- all 74 unit tests pass, including an explicit release-surface assertion that
  the deferred hero-select badge authoring pipeline is absent;
- the Release runtime builds as assembly `0.9.6.0` and passes the compiled
  runtime behavior tests;
- the frozen manager passes its embedded-runtime self-test, imports the
  preserved 0.9.3 skin pack, and remains running in the window-start smoke;
- the installer reports File/Product Version `0.9.6`;
- the release workflow marks only tags ending in `-experimental` as
  prereleases, so `v0.9.6` is a normal GitHub release.

Local artifact SHA-256 values:

- runtime DLL:
  `c5fa8ce43279cb6c3e401187d2b4c92668e66c715787b2b0f5a00ef600f9f47e`;
- frozen manager:
  `b987fc2b7fa8845795f55d3322838d779ecb18ff61afb18f4e21f353c9f56847`;
- installer:
  `bcd2fb5da6161fe7b6b66c15fe1f8dfc2cfe5001cfcd446fb496f4b238b2ef75`;
- portable ZIP:
  `2eb35ddac589e7815cf38fb19ab477cd5f1f4f7bcc1a6aae292c860683fb40d4`.
