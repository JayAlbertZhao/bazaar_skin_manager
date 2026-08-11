# Changelog

## 1.4.6

- 逐槽位模式把编辑预览与游戏原版素材并排显示；两侧使用相同的输出画布、比例、边界和中心参考线，可直接对照人物、家具与道具的位置和大小。
- 原版参考从当前适配器的已验证 Texture2D 路由按需读取并缓存；游戏已部署皮肤时优先读取 Manager 保存的原生备份，避免把当前皮肤误当作原版。
- 对局头像等运行时槽位可从同一英雄 Bundle 导出静态原图；原生为动态 Spine 或分层材质、没有可信静态位图的槽位会明确说明，不用近似图片冒充位置基准。

## 1.4.5

- 自动更新改为每次启动各检查一次 GitHub 正式版，移除六小时缓存；检查仍在后台异步执行，下载和安装仍须用户确认。
- 错误反馈简化为一键复制脱敏诊断，移除 GitHub Issue 跳转；用户可直接将剪贴板内容粘贴到 IM 发给维护者。
- 软件仍不会自动上传日志，错误记录继续保存在本机并隐藏用户名路径、Steam ID、邮箱与常见凭据。

## 1.4.4

- 设置页新增 GitHub 正式版更新检查；默认在启动后异步检查并以六小时为缓存窗口，发现新版本时由用户确认下载和安装，不会在后台静默执行安装。
- 更新器只接受本项目 GitHub Release 中与版本号精确匹配的 EXE，并同时核对 GitHub asset digest、发布的 `.sha256` 文件、实际下载大小和本地 SHA-256；校验失败不会启动安装程序。
- 错误日志改为带 UTC 时间的有界追加记录，不再由后一次异常覆盖前一次异常。
- 设置页新增“复制脱敏诊断”和“复制并打开反馈页”；报告会移除本机用户名路径、Steam ID、邮箱以及常见凭据，然后由用户检查并粘贴到预填的 GitHub Issue。
- 后台操作失败时可直接复制诊断并打开反馈页。当前版本不会静默上传日志，也不会在客户端内置 GitHub Token；后续如启用自有域名收集器，将继续使用明确同意、可预览和服务端限流的接口。

## 1.4.3

- 皮肤管理器现在内置并校验官方 BepInEx 5.4.23.5（Windows x64）。目标游戏没有模组加载器时，部署会自动补齐 BepInEx，不再要求安装 BazaarPlusPlus，也不会安装 BazaarPlusPlus 插件；已有 BepInEx 与其他插件保持不变。
- BepInEx 检测从单个 `BepInEx.dll` 扩展为 Doorstop、Preloader 与核心文件的完整启动链检查，部署记录会注明加载器来源和版本，故障提示改为皮肤管理器自身的安装/修复信息。
- 修复人物源图被错误标记为“权威透明通道”时跳过背景处理的问题；全不透明 PNG/JPEG 会回到背景移除流程。中央立绘允许用户主动放大到 90.55% 等较高画布覆盖率，仍由透明边框校验阻止整张不透明背景混入。

## 1.4.2

- 动画导入页现在可以直接选择完整的 Spine ZIP 包，不再要求用户手动解压并逐个选择 JSON、Atlas 和纹理文件。
- ZIP 导入会自动识别 Spine 4.1/4.2 运行时版本，校验默认皮肤与动画，并把多页 Atlas 归一化为可移植的单页纹理；嵌套的源图和 `.spine` 工程文件不会干扰运行时资产识别。
- 增加冻结版 EXE 的 Spine 包冒烟测试入口；已使用三页 Atlas 的 `维琳娜.zip` 验证 Spine 4.1.24、`loop` 动画、`default` 皮肤及 2048×5782 合并纹理。

## 1.4.1

- Windows 安装包升级时会清理早期版本遗留的独立素材生成器与 Spine 管理器 EXE；两项功能仍完整保留在统一的皮肤管理器中，安装目录只保留一个正式应用程序入口。

## 1.4.0

- 全部八名英雄现在都具备经过 Steam build `24570932` 序列化路由校验的 17 个英雄语音逻辑槽位；菜单选择和装备语音作为共享槽位一并提供，Mak 独有的商人语音保持只在 Mak 目标上生效。
- 语音素材改为按 `logical_slot` 作为可移植身份：Mak/KTN 语音包部署给 Vanessa、Pygmalien、Dooley、Jules、Stelle、Karnok 或 The Dragons 时，Manager 会保留 WAV 变体并重写为目标英雄的精确 FMOD GUID、路径和 selector，不再降级为纯视觉包。
- 语音生产 ZIP 不再要求源英雄与工作区英雄相同；导入后立即绑定目标英雄适配器。文件名可以继续保留 `Makxxxx`，运行时不依赖文件名判断英雄。
- Runtime 移除 Mak 单英雄限制，并补齐 `PygAudioSO -> Pygmalien`、`TheDragonsAudioSO -> Hero8` 的显式别名；英雄选择、装备语音及局内英雄语音均按当前己方英雄包路由，未知或不完整路由继续回退原声。
- 新增不含游戏音频载荷的 `manager/audio-route-catalog.json`，公共 EXE/ZIP 会携带该精确元数据；语音语义校验器和 JSON Schema 同步支持全部八名英雄。

## 1.3.3

- 逐槽位模式现在把英雄选择图标识别为“原生框 + 人物”合成槽位：从自动草稿进入时恢复原始人物层，人物可在徽章内部单独移动和缩放，底座、上下框及下框遮挡顺序继续使用经过校验的原生模板。
- GitHub 公共安装包未携带游戏衍生徽章像素时，制作器会从用户当前安装的 The Bazaar 中一次性提取并缓存同一套徽章模板；逐槽位和默认模式共用该本地资源，不再依赖维护者构建目录。
- 游戏内头像与小型头像预览现在在背景和人物之上显示原生三侧内框遮挡；人物输出使用同一左、下、右裁剪边界且顶部保持开放，框仅用于所见即所得预览，不会错误烘焙进运行时前景纹理。
- 战斗前、一天开始和一天结束的平面立绘不再使用固定的 17% 屏幕高度上移；运行时改为测量原生 Spine 与替换 Sprite 的实际投影下缘并对齐，避免替换立绘最低点高于原生挂载位置。
- 逐槽位直接导入角色立绘时默认以下缘为画布锚点，缩放后向上生长，减少透明画布比例不同造成的底部漂移。

## 1.3.2

- 皮肤制作过程现在可随时在“默认 / 草稿模式”和“逐槽位模式”之间切换；进入逐槽位模式时会自动把当前表单、素材和实时调整生成到同一个草稿工作区，不再要求先“导入到皮肤库”。
- 从逐槽位模式返回默认模式时会先保存当前每槽素材与位置；若默认草稿没有变化，再次进入逐槽位模式会继续原有微调，不会用自动生成结果覆盖它。只有默认素材或参数发生变化时才重建逐槽位起点。
- 默认模式的“导入到皮肤库”保持独立发布路径：满意时可直接发布并返回皮肤包管理，无需经过逐槽位模式；Manager 内嵌生成不再删除共享工作区目录，避免往返切换时误删逐槽位源素材。

## 1.3.1

- 自动生成模式与逐槽位模式现在共用同一个 Manager 工作区缓存；自动模式完成草稿后会直接进入逐槽位模式，无需重新导入每个输出。
- 自动生成的槽位成品图会成为对应逐槽位槽位的初始素材；对局头像按已有的独立背景与透明人物前景恢复成两层，其余支持背景的槽位同时预填原始背景和人物素材，用户可按需切换为分层合成。
- 逐槽位保存会先把生成缓存归档到 `authoring/manual_inputs`，再重建输出，避免同工作区清理旧成品图时丢失像素；自动模式的原始输入与调整记录保存在 `automatic_draft`，可继续往返编辑。

## 1.3.0

- 皮肤制作页新增“逐槽位模式”，与原有自动生成模式并列；可直接为适配器声明的每个图像槽位导入成品图。
- 每个槽位保存独立的素材、X/Y 位移和缩放，不再共享全局人物参数；预览、生成、导入皮肤库、ZIP 导出与再次编辑使用同一份配置。
- 需要背景合成的槽位支持分别导入背景层和人物层，两层可独立移动、缩放；对局头像继续输出独立的 `portrait_background` 与透明人物前景。
- 逐槽位制作源图随皮肤包写入 `authoring/manual_inputs`，编辑已有皮肤时自动回到逐槽位模式并覆盖同一稳定 ID。

## 1.2.10

- 对局头像的人物层现在以原生头像内边框为裁剪标准：只建立左、下、右三侧遮挡，顶部保持开放，允许头发、帽子和道具越过上边框；裁剪后仍输出通用的 1024×1024 方形画布，实时预览与正式生成使用同一蒙版。

## 1.2.9

- 修复非 Dooley 职业继承通用输出图层时的生成失败：当职业适配器明确不声明投影套索时，`character_shadow` 现在按透明空图层处理，与实时预览保持一致，不再阻止商店图、收藏头像等资产导出。

## 1.2.8

- 对照 Steam build `24570932` 的英雄、怪物和商人 `Portrait` / `PortraitBG` 原始资产确认头像契约：对局头像由独立的 1024×1024 背景层与透明人物前景层组成，原生裁剪边界是完整方形画布，外层边框与遮挡由游戏 UI 负责；不再把 Mak 专用边框误当成所有职业的通用蒙版。
- 皮肤制作页的“商店 / 对局头像”改为背景与人物的实际分层合成预览，同时继续导出独立的 `portrait_background` 与 `portrait_gameplay`，避免把方形背景烘焙到前景后越过原生边框层级。
- 新增背景 X/Y 取景与 100%–300% 缩放：以 cover 为最低缩放进行裁剪，位移自动钳制到源图范围，保证任何比例的背景都不会在 1024×1024 头像画布内露出空边；配置、编辑恢复和生成元数据会保留这些参数。

## 1.2.7

- 修正小图标输入优先级：只要用户提供的小图标文件存在，预览与生成流水线就必须使用它；“未提供时不生成”只作为输入为空时的回退，不再覆盖有效文件。
- 将原“留空（不替换）”改名为“未提供时不生成”，并在人物或小图标扣色后完全透明时阻止导入、提示关闭扣色或降低容差，避免把空图带进流水线。

## 1.2.6

- 修复从皮肤库编辑旧生成器皮肤后，生成前清理输出工作区会同时删除被恢复人物原图、最终触发 `FileNotFoundError` 的问题；编辑素材现在先复制到独立会话目录，再执行生成和覆盖导入。
- 新建项目切换职业时，自动生成的稳定 ID 会从 `local.dooley.<随机值>` 同步更新为所选职业，例如 `local.pygmalien.<随机值>`；手工 ID 和正在覆盖编辑的旧包 ID 保持不变。

## 1.2.5

- 皮肤管理页的“编辑”现在直接进入皮肤制作页，并恢复原皮肤包的名称、稳定 ID、版本、目标职业、人物/背景/小图标源素材及画布调整；再次加入皮肤库会更新同一个皮肤包。
- 生成器皮肤包现在将可编辑的 `authoring/inputs` 一并写入 ZIP 与资产索引，跨会话、跨电脑导入后仍可继续编辑。
- 对 1.2.4 及更早版本未打包原始输入的生成器皮肤，编辑时会按 authoring SHA-256 从本地生成缓存和一级素材库恢复原图；无法恢复的旧包会明确要求补充人物原图，不会拿渲染成品冒充源素材。

## 1.2.4

- 从 Steam build `24570932` 的原始 Unity Bundle 中补齐 Dooley、The Dragons（内部 ID `Hero8`）和 Karnok 的英雄选择徽章，部署页不再回退为空金色盾牌。
- 增加完整性测试，要求英雄目录中的每个职业都具有可打包的原皮肤缩略图资源。

## 1.2.3

- 修复仅配置 `portrait_gameplay`、未配置可选 `portrait_background` 的轻量皮肤包无法替换己方底部头像的问题；两个 Encounter 视觉槽位现在独立应用，未配置的槽位保留原生资源。
- 保持对战头像所有权隔离：只有 `BoardBuilder.LoadHeroPortraitAsync` 证明为己方的 `GenerateEncounterData` 结果会被修改，对手与未知所有者继续保留原生头像。
- 兼容 Steam build `24570932` 的原生动态头像优先级：仅在己方确实配置静态头像替换时禁用生成结果中的动态头像引用。

## 1.2.2

- 修复多皮肤同时启用时，全局 UI 扫描器可能绕过本地玩家所有权检查、错误替换对手游戏内头像的问题。
- 游戏内头像、头像背景和小头像改为只由能够确认本地玩家身份的专用加载路径处理；商店、收藏等非对局界面保持原有多皮肤路由。
- 兼容 Steam build `24570932` 的新版 SkinEdit 字段形态，并恢复经过验证的 PvP、一天开始和一天结束立绘挂载层级及遮挡关系。

## 1.2.1

- 修复一天结束与新一天开始界面的立绘未正确显示：复用已验证的 Spine 挂载路径，在原 placement 层级内按实际渲染摄像机投影定位、测量和缩放。
- 世界立绘继续作为原 Spine placement 的子节点，由原层级控制转场遮挡和生命周期，避免脱离节点后穿过漩涡并残留到棋盘画面。
- 消除新版 `HeroSelectDisplay` 缺失旧字段时产生的重复 Harmony 警告，保留有效诊断日志。

## 1.2.0

- 通过 Steam 将兼容基线从 build `24001960` 扩展到 `24570932`：复核并登记七个既有职业的新原始 Bundle 哈希，新增 The Dragons（Rin & Jin / `Hero8` / `DRA`）默认皮肤适配器，并保留旧构建兼容。
- 将新构建的游戏指纹、英雄枚举、原生纹理名称、尺寸和更新流程记录到兼容性笔记；未知构建、未知 Bundle 哈希或缺失原生对象仍保持 fail-closed。
- 按完整制作与使用流程重构为五个一级界面：皮肤部署、皮肤管理、皮肤制作、动画导入、设置；默认打开皮肤部署，启动游戏固定在顶部并始终通过 Steam。
- 部署页使用“自定义皮肤 → 被替换的游戏原皮肤”图像映射卡，同一皮肤包可映射多个经过验证的职业皮肤；计划状态与实际部署状态分离。
- 皮肤管理拆分为皮肤包管理与一级素材管理。皮肤包和人物原图、背景、小图标、音频、Spine 等素材分别拥有稳定 ID、缩略图、搜索、筛选、引用和删除保护。
- 素材包制作器内嵌为皮肤制作页，输入支持文件、拖放、剪贴板和一级素材库；生成结果可直接导入皮肤库或导出到指定 ZIP。
- Spine 4.1/4.2 JSON 通过既有校验器归一化后进入一级素材库；动画导入不再直接修改游戏，部署统一由皮肤部署页提交。
- 皮肤包编辑器恢复图像槽位、音频路由、原版对比和固定清空入口，并使用草稿事务，离开时可保存或放弃。
- 皮肤包 ZIP 可携带去重后的一级素材与引用索引，跨电脑导入后恢复可编辑来源；旧工作区会迁移原图、小图标、音频和 Spine 引用。
- 设置页自动定位 Steam 游戏目录，同时提供手动定位、构建号与适配器覆盖状态。

## 1.1.3

- 默认首页改为“部署”；资产包库用真实素材缩略图展示本地资产包，部署页按被替换皮肤组织资产选择和启用状态。
- 资产包不再绑定单一职业；同一个资产包可以映射到多个经过验证的职业皮肤，部署时再与目标适配器生成独立运行时包。
- 资产包库支持搜索、导入完整资产包 ZIP、添加现有工作区、编辑、应用到多个目标、打开目录和删除；外部工作区只从管理器移除，不递归删除用户目录。
- 资产包导入页不再要求填写职业；职业与皮肤只在部署页或“应用到…”对话框中选择。
- 删除已部署资产包会被阻止；删除当前正在编辑的工作区时，管理器会先切换到其他有效工作区，避免留下失效状态。
- 两张管理表均提供横向和纵向滚动条，主要操作固定在表格外，保持在最小窗口尺寸下可见。

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
