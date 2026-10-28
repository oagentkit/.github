# 贡献指南 — oagentkit

感谢参与 oagentkit（Open Agent Kit）模块矩阵。本文件是 **全 org 共享** 的贡献约定；
13 个业务模块仓更细的工程红线见各仓根目录的 `AGENTS.md`，
基建仓（`.github` / `matrix-tools` / 门面 `agentkit` / 文档站）见各自 README 与本仓的
[AGENTS.md](https://github.com/oagentkit/.github/blob/main/AGENTS.md)。

## 矩阵结构

贡献不是单仓事务 —— 13 个仓按 tier 严格自底向上，依赖方向是硬不变量：

| tier | 模块 |
|---|---|
| 0 | `guardx` `runtime` `textx` |
| 1 | `promptx` |
| 2 | `llm` `mcpkit` `obs` `queue` `rag` `skillkit` `toolgate` `tools` |
| 3 | `agent` |

tier N 的模块**不得**依赖 tier > N 的模块。

> 上表与 `matrix-tools/modules.json`（单一数据源）逐仓一致，由本仓
> `scripts/verify_profile_tiers.py` 在 CI 里比对。**改分层请先改数据源**，
> 不要手改这张表。

## 流程一览

```text
  发现问题 / 有想法
        │
        ▼
  开 Issue（标题: [模块] 简述）
        │
        ▼
  认领 / 开分支 / 提 PR
        │
        ▼
  CI 门禁 (go-ci.yml) + 人工 review
        │
        ├── 需修改 ──► 更新 PR ──► 回到审查
        │
        ▼
  合入 main
        │
        ├── issue 关联关闭
        ├── 发版打 tag ──────► matrix-tools 刷新 versions.json
        │                        ──► 拷到站点副本 + 同步站点 data/modules.json
        └── 安装行/文档 ─────► matrix-tools 对齐
```

## 开 Issue

1. **选对仓库**：问题落在哪个模块就开在哪个仓；跨模块/矩阵拓扑问题开在
   [matrix-tools](https://github.com/oagentkit/matrix-tools)。
2. **标题带模块前缀**：`[llm] …`、`[mcpkit] …`、`[matrix] …` —— 便于跨仓检索归类。
3. **说清现状与建议**：我们偏好「现状（版本号 + 复现/证据）→ 影响 → 建议 → 验收」四段式。
4. **用模板**：仓库提供了 Bug / 特性 / 文档三类 issue 模板，按需选。

## 提 PR

1. **先搜后提**：搜一下目标仓的 open issue，已有就评论认领，别开重复的。
2. **一个 PR 一件事**：feat 与 refactor 不要搅在一起；0.x 阶段允许 breaking，但必须写进 CHANGELOG。
3. **过 CI**：共享门禁 `oagentkit/.github/.github/workflows/go-ci.yml`
   （gofmt / golangci-lint / vet / race / govulncheck / 覆盖率）。
   本地至少跑通 `go test -race ./...`。
4. **对齐既有风格**：Option 命名、错误哨兵、`AGENTS.md` 红线以目标仓为准；
   新增公共 API 请补 godoc 与测试。

## 发版

- 版本方案 SemVer；0.x 阶段 minor 可 breaking，patch 只修不破。

### ⚠️ 顺序：CHANGELOG 必须在 tag **之前**

**第 1–3 步（单仓内，顺序不可反）**

```
特性 PR 合入 main
  1. 预写待发版段：python3 matrix-tools/scripts/gen_changelogs.py --repo <仓> --root <各仓父目录> --next-version vX.Y.Z
  2. 提交 CHANGELOG.md（走 PR 或直推都行）
  3. 打 annotated tag vX.Y.Z（**落在那个 CHANGELOG 提交之后**）
```

**为什么不能反**（实测 2026-10-20，matrix-tools#17/#24/#28）：

下游 `go get` 拿到的是 **tag 快照**。如果先打 tag、再补 CHANGELOG，
tag 快照里的 CHANGELOG.md 就**缺该 tag 自己的版本段**——发布说明残缺。
`verify_changelog_at_tag.py`（T2）会当场抓出，而 tag 受 `protect-tags` ruleset
保护**不可重打**，只能记进 `matrix-tools/scripts/changelog_tag_debt.json` 欠账基线。
实测这一条错误顺序已经制造了 **27** 条存量欠账（`llm@v0.13.0`、`agentkit@v1.8.14` 等）。

**预写还有两个前提**（#28 实测，缺一仍会破）：

1. **预写必须晚于特性合入**：先在 main 上预写、再合特性分支，预写时区间里还没有
   特性提交，段里会写成「与上一版相比无代码差异」，事后重生成却多出特性段 → 不一致。
2. **走 PR 也没问题**：#28 起版本段剔除「只改 CHANGELOG.md 的提交」与「merge commit」，
   所以「预写 → 提交 → 走 PR 合并 → 打 tag」与「直推打 tag」产出**逐字节一致**的 CHANGELOG。

**第 4 步（跨仓，刷新版本矩阵）**

```
4. 刷新矩阵源：python3 matrix-tools/scripts/release.py --root <矩阵根> sync-matrix
   → 提交 matrix-tools/versions.json（**先合源**）
   → 提交站点仓：docs/public/versions.json（versions.json 的逐字节副本）
                + data/modules.json（矩阵源 modules.json 的逐字节副本）
```

`sync-matrix` 会把**两份**站点副本都拷好。纪律不变：**源先、副本后**——
矩阵源的 PR 先合，站点仓的 PR 后合（工具只做本地拷贝，两次提交由人分两次做）。

**⚠️ 别再手跑 `gen_versions.py --org oagentkit` 来刷矩阵源**（2026-10-26 改口径）：
它**不带本地根** ⇒ 算不出「抬地板」（floorBumps 要读各仓**相邻两个 tag** 处的
go.mod 差异），只能「搬运既有值」⇒ **门面每次抬地板，矩阵源都少一条
「下游必须跑 `go mod tidy`」的登记**（issue matrix-tools#133 实测：
138 条只算出 137 条，被吞掉的正是刚发那一版）。

`sync-matrix` 用的是 `--floor-root`：**只**把 floorBumps 切到本地现算，
breakingIndex / depFloor 仍走联网口径（本地克隆可能比远旧，让它们走本地会**倒退**）。
各仓克隆必须**完整**（不是浅克隆、tag 要齐）；取不到时工具会退化成「搬运」
并在报告里报**黄灯**——那意味着抬地板的登记可能少了几条，
先把各仓 `git fetch --unshallow --tags` 再重跑。

**为什么第 4 步不能反**（实测 2026-10-21）：

**源是权威、副本是派生物**。矩阵源 `matrix-tools/versions.json` 是权威，
站点 `docs/public/versions.json` 是它的**逐字节副本**（`verify_versions_drift.py`
要求两者全等，含 `latest`）。所以必须**先刷源、再拷副本**：

- 先改副本 → 逐字节门禁判红（副本 ≠ 源）；
- **更隐蔽的一条**：源陈旧时，`verify_versions_drift.py` 给的修法文案方向是**反的**——
  它只会说「把源 cp 到站点」，于是把**陈旧的源**覆盖到站点，
  漂移从「源旧」变成「源旧 + 站点旧」。实测 2026-10-21：
  矩阵源的门面 `latest` 还是 `v1.8.20`，而 `agentkit` 最新 tag 已是 `v1.8.21`——
  源陈旧、站点副本反而更新，此时照抄门禁文案只会把陈旧扩散到全网。

**第 4 步的连带义务**：站点 `data/modules.json` 与 matrix-tools `modules.json` 同源。
矩阵新增包或改分层时必须同步它，并重跑站点的生成器
（`python3 scripts/gen_module_pages.py --check` 校验生成物是否已更新）。
实测 2026-10-21：站点 `data/modules.json` 曾缺 `llm/port`（站点副本落后于矩阵源）——
就是这一步缺失的直接后果。

现在这一步由 `release.py sync-matrix` **自动完成**（它会把 `data/modules.json` 一并拷过去，
并在报告里说明拷了没拷、是否本来就一致），且 `verify_modules_copy.py` 会
**逐字段比对**两份 ⇒ 漏了会判红，不再是「纯手工动作、漏了没人报」。

**⚠️ 重生成 CHANGELOG 会吞掉待发版段**

`gen_changelogs.py` **无参重生成只按已存在的 tag 切段**，不产出「待发版」段。
于是第 1 步预写的段会被整段吞掉，第 3 步的 tag 快照又缺自己那一段——
直接复制回第 1 步犯的错。`--next-version vX.Y.Z` 是**唯一**能产出待发版段的开关：
任何时候要重生成 CHANGELOG，都必须带上它，否则等于撤销第 1 步。

- oagentkit 当前**没有嵌套模块**（所有包都是三层路径 `github.com/oagentkit/<repo>/<pkg>`）。
  将来若引入嵌套子模块，各自独立 tag 并在文档中注明归属。

### ⚠️ 破坏式发布：提交正文里写一行 `BREAKING CHANGE:`

矩阵对下游公开的**破坏性变更清单**（`matrix-tools/versions.json` 的 `breakingIndex`，
站点与 llms.txt 直接引用它）是从各仓 `CHANGELOG.md` **自动抽取**的，判定规则见
`matrix-tools/scripts/breaking_index.py`；CHANGELOG 的正文又来自**提交正文**
⇒ **破坏式发布必须在提交正文里写一行 `BREAKING CHANGE: …`**（conventional commit
的 trailer 形态），它才会被登记到 breakingIndex。

**为什么这条是硬要求**：矩阵漏登记 = **对下游说谎**。实测 2026-10-28
（mcpkit v0.9.0）：版本段里明确写着两处破坏（`Allow` 缺省反转、工具暴露名带
server 命名空间），但提交正文只用了中文散文「破坏式变更（…）」⇒ 抽取器与独立
oracle **都**收不到 ⇒ 矩阵说「mcpkit 无破坏性变更」⇒ 下游照矩阵升版 ⇒ 配置里
`allow: []` 的那个 server 从「拿到全部工具」**静默变成「一个工具都拿不到」**，
而配置里看不出任何变化。

**记在哪**：提交正文（squash 合并后它就是发布说明的正文）。多个提交的 PR，
squash 后的正文来自 PR 正文 ⇒ 两处都写。

**兜底**：判定规则已补「行首中文声明」（`破坏性变更` / `破坏式变更` 顶格成行）
作为第二条识别路径（matrix-tools#150）——但那是**兜底**，不是主路径：
它只认行首、排除否定形态（`破坏性变更：无`）与核对记录（`破坏性变更核对（…）`）。
**主路径永远是英文 trailer**，它的判定最窄、最不会误伤。

**注意**：已发布 tag 的 CHANGELOG 段是**快照**（提交正文渲染而来）⇒ 漏标记的版本
**改不了**（tag 不可重打）⇒ 只能靠判定规则的兜底路径追溯登记，或在下一版里
写明。⇒ 标记要在**发布前**写。

## 矩阵卫生

矩阵不变量由 [matrix-tools](https://github.com/oagentkit/matrix-tools) 的脚本判定：

- **分层方向** —— tier N 不得依赖 tier > N：
  `scripts/verify_import_direction.py`（读**真实 import 图**，不是声明图）。
- **覆盖度** —— `go list ./...` 的每个包都登记在 `modules.json`，无幽灵条目；
  待退役包必须显式登记：`scripts/verify_ghost_packages.py`。
- **依赖闭包** —— 声明的 `dependsOn` 必须覆盖实际跨仓依赖：
  `verify_import_direction.py` 的「未声明 / 陈旧声明」判定。

✅ `verify_import_direction.py` **已接进 CI**（2026-10-21 起）——它跑在 matrix-tools 的
`matrix-local-guards` job（`matrix-tools/.github/workflows/matrix-tools-ci.yml`），
触发条件是 `pull_request` / `push` / `schedule` 三种都有：
**分层方向违规会真的挡住合并**，不必等人工巡检。
它在 `matrix-tools/scripts/invariants.json` 里登记为三条不变量
（`tier-direction` / `dependency-declaration` / `facade-not-consumed-internally`），
「承诺 ↔ 判据 ↔ 接线」由 `verify_inventory.py` 守。

它的前身 `scripts/verify_split.py`（覆盖度 / 分层方向 / 依赖闭包 三项）只校验**声明图**，
而声明图会漂（`llm` 声明依赖 `obs`/`runtime`，代码里早已不引）——
该脚本**已于 2026-10-21 随守卫体系分层化删除**（matrix-tools#52），由
`verify_import_direction.py` 取代。**别再照它的声明图口径造判据。**

本地复算（可选；CI 已经跑过）：

```bash
python3 matrix-tools/scripts/verify_import_direction.py --root <各仓父目录>
```

另外人工保持：

- README 矩阵表与 `modules.json` 一致（profile 的 tier 表已由本仓
  `scripts/verify_profile_tiers.py` 在 CI 里逐仓守住）
- 安装行版本 = 最新 tag
- `AGENTS.md` / `CHANGELOG.md` 在场且不过期
- 不出现已废弃 import 路径（`git.enjoye.top/enjoydream/agentkit`、门面仓 `oagentkit/agentkit`）

改动矩阵的正确姿势：改 `matrix-tools/modules.json` → 跑
`verify_import_direction.py` 校验（真实 import 图）→ 跑 `gen_docs.py` 重新生成
各仓 README/AGENTS。**不要手改各仓文档**，会与矩阵漂移。

## 行为准则

理性、就事论事、对代码严苛对人温和。issue/PR 评论只谈技术事实；
有分歧时用可运行的对比（基准/语义测试/依赖图）说话。
