# AGENTS.md — oagentkit/.github

> org 规则仓——org profile + 全 org 共享的 CI 门禁（reusable workflow）+ 规则脚本
> org：[oagentkit = Open Agent Kit](https://github.com/oagentkit)
> 本仓**不是**矩阵模块：它不在 `matrix-tools/modules.json` 里，也没有 Go module

## 本仓包含

```
profile/README.md                         # org 首页 profile（渲染在 github.com/oagentkit）
.github/workflows/go-ci.yml               # 共享 reusable workflow —— Go 门禁**规则本体**
.github/workflows/issue-auto-assign.yml   # 新 issue 自动分配（可 reusable）
.github/workflows/repo-ci.yml             # 本仓自身 CI（脚本语法 / 跨仓漂移 / YAML 可解析）
.github/workflows/workflow-lint.yml       # 本仓 Actions 语法自检（actionlint）
workflow-templates/go-ci.yml              # 组织模板：新仓的**薄调用层**
workflow-templates/issue-auto-assign.yml  # 组织模板：新 issue 自动分配
templates/dependabot-gomod.yml            # 模块仓 dependabot 模板
scripts/sync-callers.sh                   # 模块仓 ci.yml → 薄调用层 批量迁移
scripts/sync-dependabot.sh                # 模块仓 dependabot 批量同步
scripts/sync-project-hooks.sh             # 项目板 hook 批量同步
scripts/sync-projects.py                  # 项目板同步
scripts/verify_profile_tiers.py           # profile 分层表 vs modules.json 漂移门禁
CONTRIBUTING.md / SECURITY.md / SUPPORT.md
```

## 硬规则

### 规则本体 vs 模板（改门禁只改本体）

- `.github/workflows/go-ci.yml` 是**本体**——所有门禁规则、步骤、阈值、工具版本都写在这里。
- `workflow-templates/*.yml` 是给新仓用的**薄调用层模板**：内容只有
  `uses: oagentkit/.github/.github/workflows/go-ci.yml@main` + 与默认不同的 `with`。
- 各模块仓仓内的 `.github/workflows/ci.yml` 是**调用层**，只许写「引用哪个本体 + 本仓特有参数」。
- **改门禁规则只改本体**，不要去改各仓的 `ci.yml`——改本体一处生效全 org，
  改调用层只会让那个仓偏离全 org 口径。
- 调用层与模板**有差异是正常的**（如 `go-version: '1.26'`）；差异只允许出现在调用层。

### `@main` 引用 = 全 org 单点

- 各仓以 `uses: oagentkit/.github/.github/workflows/go-ci.yml@main` 引用本仓。
- ⇒ **本仓的 main 是全 org 的单点**：本仓 main 上的任何改动，会**同时**改变
  13 个模块仓 + 基建仓的下一次 CI 运行结果。
- 因为引用的是 `@main`（不是 tag），**没有「回退到旧版本」这个选项**——坏了就是全 org 一起坏。
- 改动前必须逐条确认：

  1. **向后兼容**：新增 step / input 必须带默认值，老调用层不传也能跑；
  2. **可回滚**：破坏性改动要么先加开关（input 默认关），要么先把各仓引用切到 tag 再动本体；
  3. **影响面**：改 lint 规则 / 覆盖率阈值 / go-version 会让**所有**仓同时变红——先在单个仓试点。
- 需要锁定时改为 `@v1` 这类 tag 引用（代价是要批量改各仓的 `ci.yml`）。

### 改 profile 必须与数据源对齐

- `profile/README.md` 是 org 的门面页（人类与 AI Agent 的第一入口），它给出的分层必须为真。
- 分层表写在生成区标记 `<!-- oagentkit:generated:begin -->` / `<!-- oagentkit:generated:end -->` 之间，
  数据源是 `matrix-tools/modules.json`（矩阵单一数据源）。
- **改分层先改数据源**，然后在本仓跑 `python3 scripts/verify_profile_tiers.py --write` 重生成。
- CI（`.github/workflows/repo-ci.yml` 的 `matrix-docs` job）逐 tier 逐仓比对，漂了判红。
- 历史上这里漂过 5 个仓（`queue` 被写在 tier0、实为 tier2；`obs`/`skillkit`/`mcpkit`/`rag`
  被写在 tier1、实为 tier2），而当时**没有任何门禁会报出来**——这就是生成区 + 门禁的由来。

### 文档纪律

- **不承诺没做的事**：文档里写「某脚本会强制校验」时，要么它真的接进了 CI，
  要么写明「人工核对」并给出核对命令。
  门禁正文与接线**必须成对**——「文档说有门禁、实际没接线」是本仓反复出现的坑
  （`verify_split.py` 即将退役删除：它只校验声明图且数据路径指向已退役的 agentkit 单仓，
  实跑报 `FileNotFoundError`；`verify_import_direction.py` 读真实 import 图，但尚未接进 CI）。
- **矩阵数据只认 matrix-tools 仓**：`modules.json`（分层）、`versions.json`（版本矩阵）、
  `gen_llms_txt.py`（llms.txt）。本仓**不另存副本**——副本就是下一次漂移的起点。

## CI：失败了先看哪个 job

### 各模块仓（跑的是本仓 `go-ci.yml`）

| job | 查什么 | 常见原因 |
|---|---|---|
| `Lint` | `go mod tidy -diff` → `gofmt -l` → `golangci-lint` | go.mod/go.sum 漂移；没格式化；lint 规则不通过 |
| `Test` | `go vet` → `go test -race` → 覆盖率门禁 → govulncheck | 编译/测试失败；总覆盖率低于阈值；**govulncheck 是非阻塞报告**，只亮注解，不挡合并 |
| `CHANGELOG coverage` | 扫 git 历史算应有条目，与 CHANGELOG.md 比对 | 该仓 CHANGELOG 缺条目或缺正文（脚本在 matrix-tools 仓，跑在调用方仓里） |

排查顺序：`Lint` → `Test` → `CHANGELOG coverage`。前两个是本仓规则本体的问题，
第三个是矩阵级欠账（别在自己仓里找原因）。

### 本仓自身（`repo-ci.yml`）

| job | 查什么 |
|---|---|
| `Scripts` | `scripts/*.py` 的 `py_compile`、`scripts/*.sh` 的 `bash -n`、`--help` 冒烟 |
| `Matrix docs drift` | llms.txt / versions.json 副本 / 门面守卫 / 陈旧包引用 / 幽灵包 / CHANGELOG 覆盖 —— **跨仓漂移** |
| `YAML` | workflows + 模板 + dependabot 的 YAML 可解析 |

`workflow-lint.yml` 用 actionlint 做 Actions 语法自检，与 `repo-ci.yml` 互补。

### PR 事件 vs push / schedule 事件

- **PR 事件**：跨仓门禁**按范围裁剪**——只校验**本 PR 触及的仓**（= PR 的 base 仓），
  且用 **PR head** 的内容校验。
  只裁剪不换 ref 解决不了问题：合入前各仓 main 上还是旧产物。
  所以改 agent 的 PR 只会被 agent 的 CHANGELOG 欠账挡住，不会被无关仓的欠账卡住。
- **push / schedule / workflow_dispatch**：走**全矩阵**——周期性全量审计兜底，不可省，
  否则就是漏检（PR 只查被触及的仓，没 PR 的漂移就没人看）。
- 范围判据的**唯一逻辑来源**是 oracle 的 `--print-scope`（它读 GitHub 事件上下文），
  不要在 YAML 里另造一份判定。
- 本仓 `repo-ci.yml` 在 push 与 PR 上都跑；但 `Matrix docs drift` job 拉的是
  matrix-tools 与站点的**默认分支**，所以它反映的是「各仓 main 的现状」，不是当前 PR 的内容。

## 仓特有纪律

- **本仓的 main 是全 org 的单点**：改本体前先想清楚影响面，必要时先在单个仓试点。
- **改门禁只改本体**，模板与调用层只写差异。
- **本仓的 profile 是门面**：宁可少写一句，也不要写一句错的分层。
- **脚本改完先本地验**：`bash -n scripts/*.sh` + `python3 -m py_compile scripts/*.py`，再推。
- **`GOPRIVATE` 三件套一起写**：`GOPRIVATE` / `GONOPROXY` / `GONOSUMDB` 在 `go-ci.yml` 里
  显式全写——`GOPRIVATE` 同时蕴含后两者，但只改一个会让人误以为生效了。
- **依赖第一方模块时绕过公共 proxy**：矩阵模块是第一方依赖，走 proxy 会命中
  「tag 重置前的旧缓存」——门禁全绿而依赖的代码是错的。

## 明确不属于本仓的

| 能力 | 归属 |
|---|---|
| 矩阵分层 / 依赖 / 版本矩阵（`modules.json`、`versions.json`） | `github.com/oagentkit/matrix-tools` |
| 门禁**判据脚本**（`verify_*` / `gen_*`） | `github.com/oagentkit/matrix-tools` |
| Go 业务实现（文本 / 事件总线 / 安全围栏 / 工具池 / 编排 …） | 对应模块仓（见 profile 的 tier 表） |
| 文档站页面与站点数据 | `github.com/oagentkit/oagentkit.github.io` |
| 门面兼容层（re-export，v1.x 后退役） | `github.com/oagentkit/agentkit` |
