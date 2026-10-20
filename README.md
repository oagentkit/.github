# oagentkit/.github

oagentkit org 的 GitHub 统一规则仓：org profile + **全 org 共享的 CI 门禁（reusable workflows）**。

各模块仓库的 `.github/workflows/ci.yml` 只保留一个薄调用层，规则本体统一在本仓维护——
改一处（golangci-lint 版本、门禁步骤、覆盖率策略），所有引用 `@main` 的仓库立即生效。

本仓同时是 org 的**社区规范入口**：org profile、issue/PR 模板、安全策略、支持渠道
都在这里。

## 共享 workflow

### `go-ci.yml` — Go 统一 CI 门禁

步骤：**go mod tidy -diff** → gofmt 检查 → golangci-lint → `go vet` → 测试（race）
→ govulncheck 漏洞扫描 → 覆盖率门禁 → coverage 产物上传。

内置运维特性：同 ref 重复触发自动取消旧 run；lint/test 各有 `timeout-minutes`；
覆盖率与 govulncheck 摘要写入 Step Summary；coverage 产物保留 14 天。

| input | 默认 | 说明 |
|---|---|---|
| `go-version` | `'1.24'` | setup-go 语义，如 `1.26` 解析为最新 1.26.x。**oagentkit 全矩阵统一用 `1.26`**（各仓 `go.mod` 是 `go 1.26.0`），所以各仓的 `ci.yml` 都显式传 `go-version: '1.26'` |
| `golangci-lint-version` | `v2.14.0` | golangci-lint 版本 |
| `coverage-threshold` | `30` | 总覆盖率下限（%）；`0` = 关闭门禁 |
| `test-script` | `go test -race -count=1 -coverprofile=coverage.out ./...` | 根模块测试命令；产出 `coverage.out` 即自动接门禁 |
| `test-nested` | `true` | 自动发现嵌套 `go.mod` 子模块并 `build + test -race`。**oagentkit 当前无嵌套模块**，此项为空转；保留是为将来加子模块时不必改规则 |
| `additional-script` | `''` | 测试后的附加检查（多行 shell） |
| `govulncheck` | `true` | 漏洞扫描（pin v1.9.0）——**非阻塞报告**。扫描器自身要求 go≥1.26，oagentkit 全矩阵满足，出的是正常报告 |
| `tidy-check` | `true` | `go mod tidy -diff` 门禁，检测 go.mod/go.sum 漂移 |

### 模块仓库接入方式

```yaml
# .github/workflows/ci.yml —— 全部内容
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  ci:
    name: Lint & Test
    uses: oagentkit/.github/.github/workflows/go-ci.yml@main
    with:
      go-version: '1.26'
```

只写与共享默认不同的参数；与默认一致的字段省略。

### `issue-auto-assign.yml` — 新 issue 自动分配

新开 issue 自动分配给维护者，避免掉进无人区。本仓 `issues: opened` 即触发；模块仓可薄调用复用。

| input | 默认 | 说明 |
|---|---|---|
| `assignees` | `'zy84338719'` | 受理人 GitHub login，逗号分隔可多人 |
| `skip-if-assigned` | `true` | issue 已有 assignee 时跳过 |

规则：bot 开的 issue（dependabot 等）跳过；已分配过的 login 不重复添加。

### `repo-ci.yml` / `workflow-lint.yml`

`repo-ci.yml` 是规则仓自身的持续集成：`scripts/*.py` 语法与入口 smoke、
`scripts/*.sh` bash 语法、workflow/模板 YAML 可解析。
`workflow-lint.yml` 用 actionlint 做 Actions 语法自检，与前者互补。

### 组织 workflow 模板（`workflow-templates/`）

新仓 **Actions → New workflow** 时可直接选用：

| 模板 | 用途 |
|---|---|
| `issue-auto-assign.yml` | Auto-assign new issues — 自动把新 issue 分给维护者 |
| `go-ci.yml` | Run a continuous integration test — 薄调用共享 Go CI 门禁 |

`$default-branch` 占位符在创建时会替换为该仓默认分支。

## 矩阵健康怎么保证

CI 门禁管的是**单个仓**的质量。**跨仓的矩阵不变量**由
[matrix-tools](https://github.com/oagentkit/matrix-tools) 的脚本判定：

1. **分层方向** —— tier N 的模块不得依赖 tier > N（自底向上不变量）：
   `scripts/verify_import_direction.py`（读**真实 import 图**，不是声明图）。
2. **覆盖度** —— `go list ./...` 的每个包都登记在 `modules.json`，无幽灵条目；
   待退役包必须显式登记（不静默跳过）：`scripts/verify_ghost_packages.py`。
3. **依赖闭包** —— 声明的 `dependsOn` 必须覆盖实际跨仓依赖：
   `verify_import_direction.py` 的「未声明 / 陈旧声明」判定。

> ⚠️ 截至 2026-10-21 实测：`verify_import_direction.py` **尚未接进任何 CI**；
> `scripts/verify_split.py`（覆盖度 / 分层方向 / 依赖闭包 三项）只校验**声明图**，
> 且数据路径仍指向已退役的 agentkit 单仓（实跑报 `FileNotFoundError`）——
> **别把它当成在跑的门禁**。人工核对命令：
> `python3 matrix-tools/scripts/verify_import_direction.py --root <各仓父目录>`
>
> ⚠️ 依赖闭包这项有个实现坑：`go list -f` 的模板必须用**逗号**分隔 imports。
> 用换行会让每个包跨多行，`splitlines()` 只取到第一行（通常是 `context`），
> 校验会**静默空转** —— 看起来全绿，实则什么都没查。

## 引用 ref 策略

- `@main`（当前选择）：规则变更即时全量生效，符合「统一提交」诉求。
- 需要锁定时改为 `@v1` 这类 tag —— 本仓打 tag 后再批量改各仓引用。
- 安全边界：能推本仓 `main` 的人 = 能影响全 org CI，权限对齐 org admin。

## 运维

- **改规则**：直接改本仓 `.github/workflows/go-ci.yml`，merge 即生效，无需动模块仓。
- **升级 action 版本**：本仓 dependabot（`github-actions` ecosystem，weekly）提 PR，
  merge 即全 org 生效。
- **新增仓库**：拷上面的接入模板，改 `go-version` 即可；或跑
  `scripts/sync-callers.sh <repo-dir>` 从旧版完整 ci.yml 自动生成薄调用层。
- **矩阵变更**：改 [matrix-tools/modules.json](https://github.com/oagentkit/matrix-tools)
  后跑 `verify_import_direction.py` 校验真实 import 图，再跑 `gen_docs.py` 重新生成各仓 README/AGENTS。
- **改 profile 分层**：分层表写在 `profile/README.md` 的生成区里，数据源是 `modules.json`。
  改完数据源后在本仓跑 `python3 scripts/verify_profile_tiers.py --write` 重生成；
  CI（`repo-ci.yml` 的 `matrix-docs` job）会逐 tier 逐仓比对，漂了判红。

## 目录

```
AGENTS.md                                # 本仓的工程红线（谁在改本仓，先读这份）
.github/workflows/go-ci.yml              # 共享 reusable workflow（Go 门禁规则本体）
.github/workflows/issue-auto-assign.yml  # 新 issue 自动分配（可 reusable）
.github/workflows/repo-ci.yml            # 规则仓自身脚本/YAML CI
.github/workflows/workflow-lint.yml      # 本仓 Actions 语法自检（actionlint）
.github/dependabot.yml                   # 本仓 action 依赖自动升级
.github/ISSUE_TEMPLATE/                  # org 默认 issue 模板（bug / feature / docs / config）
.github/PULL_REQUEST_TEMPLATE.md         # org 默认 PR 模板
workflow-templates/issue-auto-assign.yml # 组织模板：新 issue 自动分配
workflow-templates/go-ci.yml             # 组织模板：Go CI 薄调用层
profile/README.md                        # org 首页 profile（渲染在 github.com/oagentkit）
CONTRIBUTING.md                          # 全 org 贡献流程
SECURITY.md                              # 安全策略 / 私密披露
SUPPORT.md                               # 支持渠道索引
scripts/sync-callers.sh                  # 模块仓 ci.yml → 薄调用层 批量迁移脚本
scripts/sync-dependabot.sh               # 模块仓 dependabot 批量同步
scripts/verify_profile_tiers.py          # profile 分层表 vs modules.json 漂移门禁
templates/dependabot-gomod.yml           # 模块仓 dependabot 模板
```
