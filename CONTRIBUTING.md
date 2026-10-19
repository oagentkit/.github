# 贡献指南 — oagentkit

感谢参与 oagentkit（Open Agent Kit）模块矩阵。本文件是 **全 org 共享** 的贡献约定；
单仓更细的工程红线见各仓 `AGENTS.md`。

## 矩阵结构

贡献不是单仓事务 —— 13 个仓按 tier 严格自底向上，依赖方向是硬不变量：

| tier | 模块 |
|---|---|
| 0 | `textx` `runtime` `guardx` `queue` |
| 1 | `promptx` `obs` `skillkit` `mcpkit` `rag` |
| 2 | `llm` `toolgate` `tools` |
| 3 | `agent` |

tier N 的模块**不得**依赖 tier > N 的模块。

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

```
特性 PR 合入 main
   → 预写待发版段：python3 matrix-tools/scripts/gen_changelogs.py --repo <仓> --root <各仓父目录> --next-version vX.Y.Z
   → 提交 CHANGELOG.md（走 PR 或直推都行）
   → 打 annotated tag vX.Y.Z（**落在那个 CHANGELOG 提交之后**）
   → 之后才更新：该仓 README 安装行 / 跑 gen_versions.py --org oagentkit 刷新 versions.json
```

**为什么不能反**（实测 2026-10-20，matrix-tools#17/#24/#28）：

下游 `go get` 拿到的是 **tag 快照**。如果先打 tag、再补 CHANGELOG，
tag 快照里的 CHANGELOG.md 就**缺该 tag 自己的版本段**——发布说明残缺。
`verify_changelog_at_tag.py`（T2）会当场抓出，而 tag 受 `protect-tags` ruleset
保护**不可重打**，只能记进 `matrix-tools/scripts/changelog_tag_debt.json` 欠账基线。
实测这一条错误顺序已经制造了 25 条存量欠账（`llm@v0.13.0`、`agentkit@v1.8.14` 等）。

**预写还有两个前提**（#28 实测，缺一仍会破）：

1. **预写必须晚于特性合入**：先在 main 上预写、再合特性分支，预写时区间里还没有
   特性提交，段里会写成「与上一版相比无代码差异」，事后重生成却多出特性段 → 不一致。
2. **走 PR 也没问题**：#28 起版本段剔除「只改 CHANGELOG.md 的提交」与「merge commit」，
   所以「预写 → 提交 → 走 PR 合并 → 打 tag」与「直推打 tag」产出**逐字节一致**的 CHANGELOG。

- oagentkit 当前**没有嵌套模块**（所有包都是三层路径 `github.com/oagentkit/<repo>/<pkg>`）。
  将来若引入嵌套子模块，各自独立 tag 并在文档中注明归属。

## 矩阵卫生

以下由 [matrix-tools](https://github.com/oagentkit/matrix-tools) 的
`scripts/verify_split.py` 在拆分/发版时强制校验，任一失败 exit 1：

- **覆盖度** —— `go list ./...` 的每个包都登记在 `modules.json`，无幽灵条目；
  待退役包必须显式登记。
- **分层方向** —— tier N 不得依赖 tier > N。
- **依赖闭包** —— 每个模块的 `dependsOn` 必须覆盖其包的实际跨仓依赖。

另外人工保持：

- README 矩阵表与 `modules.json` 一致
- 安装行版本 = 最新 tag
- `AGENTS.md` / `CHANGELOG.md` 在场且不过期
- 不出现已废弃 import 路径（`git.enjoye.top/enjoydream/agentkit`、门面仓 `oagentkit/agentkit`）

改动矩阵的正确姿势：改 `matrix-tools/modules.json` → 跑 `verify_split.py` 校验 →
跑 `gen_docs.py` 重新生成各仓 README/AGENTS。**不要手改各仓文档**，会与矩阵漂移。

## 行为准则

理性、就事论事、对代码严苛对人温和。issue/PR 评论只谈技术事实；
有分歧时用可运行的对比（基准/语义测试/依赖图）说话。
