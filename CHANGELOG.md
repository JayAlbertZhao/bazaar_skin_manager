# Changelog

## 1.1.4

- Convert Spine 3.5, 3.6, 3.7, 3.8, and 4.0 JSON packages to Spine 4.2.43
  through the same pinned converter used for verified Spine 4.1 imports.
- Preserve Spine 4.2 packages without conversion and reject versions outside
  the supported 3.5–4.2 input range.
- Record verified original Spine bundles and `catalog.bin` in a user-visible
  backup directory so a managed B replacement can be replaced directly by C.

## 1.1.3

- Spine 4.2 JSON packages continue directly through import without conversion.
- Spine 4.1 JSON packages are converted to 4.2.43 during import with the pinned
  SpineSkeletonDataConverter v3.8 binary before preview or deployment.
- Other Spine versions fail closed with an actionable 4.1/4.2 compatibility
  message instead of attempting a version-marker-only rewrite.
- The Windows build downloads the converter from its official release, verifies
  its SHA-256, bundles the license notice, and checks its presence in self-test.

## 1.1.2

- 将 Spine 动画管理器收进皮肤管理器组件页，安装版与管理器便携包均携带该组件。
- Spine Bundle、普通皮肤原生纹理与 `catalog.bin` 改由同一个多皮肤事务合成、备份、回滚和诊断；移除 Spine 替换不会移除其他已部署皮肤。
- Spine 目标跟随验证适配器覆盖 Mak、Vanessa、Pygmalien、Dooley、Jules、Stelle 与 Karnok；Karnok 的本体和 Creature Bundle 会作为同一目标共同处理。

- Accept Spine 4.1 and 4.2 JSON packages and normalize 4.1 version metadata for
  the game's Spine 4.2 runtime during deployment.
- Read the texture pages declared by the atlas instead of rejecting packages
  that contain multiple runtime PNG pages or additional source images.
- Merge multi-page atlases into the target bundle's single Texture2D while
  translating region bounds without changing the verified deployment flow.
- Cap offline setup-pose rendering at 4096 pixels and render from the normalized
  atlas so large multi-page packages remain responsive and match deployment.
- Pin deploy, restore, status, and log controls to the visible window footer so
  preview expansion and Windows DPI scaling cannot hide critical actions.

## 1.1.1

- 将素材包制作器收进皮肤管理器，作为可直接打开的内置组件；安装版和管理器便携包现在同时携带两个程序。
- 精简全局标题栏，将部署、恢复、刷新和工作区操作归入对应页面，减少跨页面按钮歧义。
- 控制中台按“工作区”和“部署”分组，并为多职业列表补齐横向、纵向滚动条。
- 编辑器侧栏按“目标与游戏”和“资产包”分组；资产包页面按“工作区”和“导出与清理”分组，保证小窗口下主要操作可见。
- 两个程序会按当前屏幕尺寸居中打开；管理器支持 Ctrl+1/2/3 切页和 Ctrl+G 打开制作器，制作器支持 Ctrl+1/2 切换素材与高级设置。
- 控制中台按“被替换皮肤”合并同目标资产包：绿色勾/红色叉可直接切换启用状态，资产包单元格提供下拉选择；部署与取消部署统一留在中台。
- 原“皮肤编辑器”调整为“资产导入管理器”，用于把一套资产保存并加入控制中台，不再在导入页面混放部署操作。

## 1.1.0

- 新增多职业皮肤控制中台，可统一查看、启用或停用多个职业工作区，并一键跳转到对应皮肤编辑器。
- 支持将多个职业皮肤一次性同时安装、诊断和恢复；安装记录升级为多资产包结构并兼容旧记录。
- 运行时改为加载全部已启用皮肤包，按英雄、皮肤和资源名称路由图像、立绘、原生纹理与音频替换。
- 同一英雄皮肤或资产包 ID 冲突会在部署前阻断，避免不确定的覆盖顺序。

## 1.0.0

- Add verified default-skin adapters for Mak, Vanessa, Pygmalien, Dooley, and
  Jules, including audited native Texture2D targets for the current Steam
  build.
- Let the asset generator select any verified hero target and reuse one
  deterministic rendering recipe with narrow per-hero output overrides.
- Publish the exact adapter ids, versions, targets, and hashes in
  `manager-build.json` so companion tools can check compatibility before
  modifying a Manager workspace.
- Replace cascaded per-slot errors for an unsupported hero with one actionable
  update/target compatibility error.
- Keep verified adapters fail-closed: asset packs still cannot supply or
  override Manager deployment contracts.
- Generalize the request-gated central-standing diagnostic to every target
  hero and verify attach, idempotence, and native-state restoration in-game.
- Make runtime audit validation understand deploy-time patched inspector
  backgrounds and explicitly scoped local-player portrait loads.
- Restore the proven Mak portrait layering contract for generated packs:
  `portrait_gameplay` is a transparent foreground, while
  `portrait_background` remains the separate lower layer. This removes the
  unwanted background in the hero-select preview and keeps the in-match HUD
  frame above the portrait art.
- Ship the Skin Manager and the independent Asset Generator as the two public
  software components. Character and artwork packs remain outside the GitHub
  release and are distributed separately.
- Keep the Asset Generator pipeline actions pinned above the window bottom and
  add vertical scrolling to both authoring tabs, including minimum-window
  layout validation in the frozen executable self-test.
- Accept sparse monochrome icons produced by the included deterministic icon
  presets without weakening the transparent-border guard.
- Default new Asset Generator projects to writing the generated pack beside
  the Asset Generator executable.

## 0.9.63

- Fix Windows startup failures caused by a packaged Tcl DLL and Tcl library
  data coming from different patch versions.
- Make the frozen-manager release self-test initialize Tcl/Tk so an invalid
  GUI bundle fails during packaging instead of on the user's machine.
- Keep the runtime adapter, skin assets, audio routing, and deferred badge
  authoring pipeline unchanged from 0.9.62.

## 0.9.62

- Restrict PvP transition art, board portraits, and late-loaded portrait
  replacements to the local player; opponent visuals retain native assets.
- Give the local board portrait a dedicated background layer sourced from the
  replacement artwork instead of exposing Mak's native portrait background.
- Normalize the external Kotone voice pack against the corresponding original
  Mak routes and retain native audio as the fail-open fallback.
- Display the manager version in both the window title and application header.
- Keep the deferred hero-select badge authoring pipeline excluded.

## 0.9.61

- Detect an existing per-user installation through the stable Inno Setup
  application identity and upgrade it in place instead of creating a parallel
  installation.
- Preserve manager workspaces and deployed-mod state while replacing the
  installed manager binary and release metadata.
- Publish the matching version section from this changelog on every GitHub
  Release; a missing section now fails the release job.
- Keep the runtime behavior and asset scope of 0.9.6 unchanged. The deferred
  hero-select badge authoring pipeline remains excluded.

## 0.9.6

- Keep `PvpScreen`, `EndOfDayScreen`, and other exact SkinEdit placements
  independent from the central hero-selection reconciler, preventing an
  attached replacement from reverting to the native hero during PvP entry.
- Discover hero/skin adapters through a fail-closed registry and preload the
  verified gameplay portrait route through its exact native Texture2D target.
- Promote the PvP result-speaker selection introduced in 0.9.5 to the stable
  release line.

## 0.9.5 (experimental)

- Choose exactly one PvP result speaker per combat: the local player or the
  opponent, with equal probability when both can speak.
- Preserve the native opponent-only behavior outside the target hero skin and
  fall back to the local player for non-verbal opponents.
- Keep this behavior on the `experimental` branch; stable `main` remains on
  the 0.9.3 audio-routing semantics.

## 0.9.3

- Prevented specialized standing/collection overlays from matching gameplay
  portrait sprites through broad substring routes.
- Restored the gameplay portrait to the verified runtime Sprite route so its
  256 PPU, full-rect geometry, and alpha envelope are applied atomically.
- Made exact asset-name matches outrank fuzzy routes regardless of manifest
  ordering.

## 0.9.2

- Track manager, runtime-adapter, and external asset-pack versions and hashes
  as independent deployment components.
- Bundle verified runtime metadata with frozen manager releases.
- Restrict central standing-art reconciliation to the actual hero-select
  hierarchy so gameplay portrait surfaces cannot receive a delayed overlay.
- Preserve deploy-time Addressables CRC repair and reversible native backups.

## 0.9.1

- Repositioned the repository as a manager-only public project.
- Removed bundled skin manifests, media metadata, and asset-production tooling.
- Moved verified hero routing into a data-only adapter contract.
- Renamed runtime namespaces, storage paths, plugin ids, and binaries to the
  generic Bazaar Skin Manager identity.
- Made source CLI install commands require an explicit external pack.
- Kept GitHub installer and portable releases free of skin art and audio.

## 0.9.0

- Added the Windows Skin Manager with partial visual/audio slot editing.
- Added complete-pack ZIP import and export.
- Added reversible deploy and restore for verified Mak default-skin assets.
- Added deploy-time Unity bundle texture replacement for first-frame surfaces.
- Added Steam installation discovery across registry, library folders, and
  conventional paths on mounted drives.
- Added manual game-folder selection and one-click Steam launch.
- Added per-user Windows installer, portable manager build, release hashes,
  safe application-uninstall restoration, and GitHub Release automation.
- Added optional Authenticode signing in the release workflow.
