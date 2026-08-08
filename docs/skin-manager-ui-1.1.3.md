# Skin Manager 1.1.3 interaction and data-model plan（已废弃）

> 本文件是 1.1.3 阶段的历史草案，不再作为产品或开发依据。它保留了后来被否决的一级“资产包导入”等设计。
>
> 1.2.0 的唯一有效规格见 [The Bazaar 皮肤管理器 1.2.0 产品与 UI 交互规格](skin-manager-product-ui-spec-1.2.0.md)。

## The modeling error in 1.1.1–1.1.3 draft

The current UI treats one workspace as all three of these things:

- an asset pack that the user owns;
- a binding to one hero skin;
- a deployable runtime package.

That coupling causes every reported interaction problem:

- the library has no useful visual identity because it is rendered as a
  deployment table;
- alternative uses of the same art require duplicate workspaces;
- importing or editing art incorrectly asks for a hero;
- the library becomes the default page even though deployment is the routine
  user task;
- changing a hero mutates the asset pack rather than changing a deployment
  assignment.

## New object model

### 1. Asset pack

A reusable content object. It owns:

- id, display name, version and cover image;
- named visual slots, audio sources and optional animation sources;
- authoring provenance and capability metadata;
- no selected hero and no enabled/deployed flag.

The cover is derived deterministically when absent. Preferred source order:
`store_image`, `collection_list`, `hero_select`, `portrait_gameplay`, then the
first readable visual slot.

### 2. Deployment assignment

A user preference that maps one game target to one asset pack:

```json
{
  "Mak|Skin_MAK_01/A": {
    "pack_path": "...",
    "enabled": true
  }
}
```

The same asset pack may be assigned to several targets. The assignment owns
the target; the asset pack does not.

### 3. Materialized runtime pack

An ephemeral target-specific package built only for validation/deployment. The
Manager combines the selected asset files with the verified adapter for the
assignment target. It never writes the chosen hero back into the library pack.

Visual slots use the intersection between pack contents and adapter slots.
Target-specific audio is included only when its route catalog is compatible;
otherwise deployment gives a narrow warning and leaves original audio in use.

## Main navigation

The first page is **部署**, because selecting/replacing/enabling skins and
launching the game are the normal repeat actions.

1. **部署** — current plan and actual installed state.
2. **资产包库** — visual inventory and lifecycle management.
3. **资产包导入** — bring an existing pack into the library; no hero field.
4. **素材包制作器** — launch the deterministic authoring component.
5. **Spine 动画管理器** — launch the animation component.

## Deployment page

```text
部署                                      [部署所选] [取消部署] [刷新]
┌ target ──────────────┬ selected asset pack ─────────────┬ state ┐
│ Dooley / 默认皮肤     │ [thumbnail] 蜥蜴与墙神        ▼   │  ● 启用 │
│ Jules / 默认皮肤      │ [thumbnail] Ranni for Jules  ▼   │  ○ 停用 │
└──────────────────────┴──────────────────────────────────┴────────┘
```

- One row per verified game target, including targets with no assignment.
- The asset selector shows thumbnail, name and version; `不替换` is always an
  option.
- Enable/disable belongs to the assignment and is directly clickable.
- Selecting an asset pack never edits that pack.
- A compact installed-state strip distinguishes “planned” from “currently
  deployed”.
- Primary actions remain visible outside the scrolling table.

## Asset-pack library

The library is a thumbnail grid, not a path-first spreadsheet.

```text
资产包库             [search] [all capabilities] [import ZIP]
┌────────────┐ ┌────────────┐ ┌────────────┐
│  cover     │ │  cover     │ │  cover     │
│ pack name  │ │ pack name  │ │ pack name  │
│ version    │ │ version    │ │ version    │
│ 10图/0音频 │ │ 8图/2音频  │ │ 10图/Spine │
└────────────┘ └────────────┘ └────────────┘
[编辑内容] [应用到…] [打开目录] [删除/移出]
```

- Image is the dominant identification signal; paths are detail text only.
- Selecting a card reveals its id, version, contents, compatibility and path.
- `应用到…` opens a target checklist and updates deployment assignments.
- Deleting a Manager-owned pack removes its assignments first after explicit
  confirmation. External packs are only removed from the library.

## Asset-pack import page

- Drop/browse a complete pack ZIP or existing workspace.
- Immediately show cover, name, version and detected contents.
- Editable package metadata: name, id and version.
- No hero or skin picker.
- Final action is `加入资产包库`.
- Assignment happens later from `应用到…` or the Deployment page.

The old target picker survives only as a migration hint for legacy packs and
is never shown as an editable asset-pack property.

## Backward compatibility

- Existing `studio.json.target` is read once as a recommended initial
  assignment.
- Existing `managed_workspaces` and `target_selections` migrate to
  `assignments`; no asset files are copied or deleted during migration.
- The legacy target remains readable so old exported ZIPs still validate.
- Library membership and target assignments live in Manager settings. The
  existing schema-1 `studio.json.target` field remains a compatibility hint for
  old packs and target-specific audio authoring; it is not used as the library
  package's deployment assignment.
- Runtime `mod.json` schema remains unchanged because target binding is added
  during materialization.

## Acceptance criteria

- The initial screen is Deployment.
- Every local pack is recognizable from a real thumbnail before opening it.
- Importing/editing a pack never asks which hero it replaces.
- One pack can be assigned to two different verified hero skins without
  duplicating its workspace.
- Deployment builds two target-specific runtime packs from that one source and
  passes existing validation/conflict checks.
- Existing 1.1.2 settings migrate without losing enabled selections.
- At 1024×640, every primary action remains visible and inventory content
  scrolls independently.
