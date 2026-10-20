# oagentkit

> **O**pen **A**gent **K**it — 开源 · 易用 · Agent 工具集合

**13 个独立发版的 Go 模块矩阵**，覆盖 Agent / LLM 编排工具链：模型客户端、MCP 工具池、
技能库、知识检索、Agent 编排、运行时基础设施、安全围栏、DB 队列。
依赖严格自底向上，全矩阵无环。

- 📦 **模块矩阵**（按 tier 自底向上）：

  <!-- oagentkit:generated:begin -->
  | tier | 模块 |
  |---|---|
  | 0 | [guardx](https://github.com/oagentkit/guardx) · [runtime](https://github.com/oagentkit/runtime) · [textx](https://github.com/oagentkit/textx) |
  | 1 | [promptx](https://github.com/oagentkit/promptx) |
  | 2 | [llm](https://github.com/oagentkit/llm) · [mcpkit](https://github.com/oagentkit/mcpkit) · [obs](https://github.com/oagentkit/obs) · [queue](https://github.com/oagentkit/queue) · [rag](https://github.com/oagentkit/rag) · [skillkit](https://github.com/oagentkit/skillkit) · [toolgate](https://github.com/oagentkit/toolgate) · [tools](https://github.com/oagentkit/tools) |
  | 3 | [agent](https://github.com/oagentkit/agent) |
  <!-- oagentkit:generated:end -->

  上表逐仓对齐 [`matrix-tools/modules.json`](https://github.com/oagentkit/matrix-tools)（矩阵单一数据源），
  本仓 `scripts/verify_profile_tiers.py` 在 CI 里逐 tier 逐仓比对——**改分层请先改数据源**。

- 🤖 **AI Agent 入口**：13 个业务模块仓根目录均有 `AGENTS.md`（权威规范与硬规则）·
  基建仓（`.github` / `matrix-tools` / 门面 `agentkit` / 文档站）的规范见各自 README 与本仓
  [CONTRIBUTING](https://github.com/oagentkit/.github/blob/main/CONTRIBUTING.md) ·
  全矩阵 [llms.txt](https://oagentkit.github.io/llms.txt) ·
  机器可读 [versions.json](https://oagentkit.github.io/versions.json)
- 🏗️ **基建**：[.github](https://github.com/oagentkit/.github)（全 org 共享 CI 门禁）·
  [matrix-tools](https://github.com/oagentkit/matrix-tools)（版本矩阵生成 / 审计 / import 改写 / 建仓 / 发版流水线）·
  [oagentkit.github.io](https://github.com/oagentkit/oagentkit.github.io)（文档站）
- 📜 **沿革**：源自 agentkit 单仓（`git.enjoye.top/enjoydream/agentkit` v0.15.4），
  2026-10-17 按框架耦合分层拆分为本矩阵；[门面仓](https://github.com/oagentkit/agentkit)
  `oagentkit/agentkit` v1.0.0 提供过渡期兼容层，v1.x 后退役。

## 快速开始

```bash
go get github.com/oagentkit/llm@latest        # 模型客户端
go get github.com/oagentkit/mcpkit@latest     # MCP 工具池
go get github.com/oagentkit/agent@latest      # Agent 编排
```

**不要引门面仓 `oagentkit/agentkit`** —— 它只是原单仓的 re-export 兼容层。
旧路径到新路径的完整映射见
[agentkit/MIGRATION.md](https://github.com/oagentkit/agentkit/blob/main/MIGRATION.md)。

## 依赖方向

```
tier 3   agent     ──► llm, textx

tier 2   llm       ──► textx
         obs       ──► guardx
         skillkit  ──► textx, guardx
         mcpkit    ──► runtime
         rag       ──► textx
         toolgate  ──► guardx, mcpkit
         tools     ──► textx, runtime, guardx
         queue     ──► （无第一方依赖；第三方地板 github.com/lib/pq）

tier 1   promptx   ──► textx, runtime

tier 0   textx / runtime / guardx ──► （零第一方依赖、零重量级第三方地板）
```

箭头 =「依赖谁」。这张图画的是**真实 import 图**（`go list` 视角，2026-10-21 实测），
不是 `modules.json` 里声明的 `dependsOn`——后者当前有 3 条陈旧声明
（`llm→obs`、`llm→runtime`、`obs→textx`），声明图落后于真实图，待 matrix-tools 修。

tier N 的模块不得依赖 tier > N 的模块 —— 这条不变量由
`matrix-tools/scripts/verify_import_direction.py` 判定（它读真实 import 图，不是声明图）。

⚠️ 截至 2026-10-21 实测，`verify_import_direction.py` **尚未接进任何 CI**；
`verify_split.py` 只校验声明图，且数据路径仍指向退役的 agentkit 单仓
（实跑报 `FileNotFoundError: docs/split-matrix/modules.json`）。
所以这条不变量目前**靠人工核对**，命令：

```bash
python3 matrix-tools/scripts/verify_import_direction.py --root <各仓父目录>
```

## 与 oekit 的关系

oagentkit 与 [oekit](https://github.com/oekit)（Open Easy Kit，Go 微服务工具链）是
**两套独立矩阵**，不是子品牌。oagentkit **依赖** oekit 的：

- `github.com/oekit/pkg` —— 微工具集（errors / stringx / encoding / mathx）
- `github.com/oekit/concurrency` —— goroutine 池 / 单飞 / 重试 / 令牌桶 / 滑动窗口 / 时间轮
- `github.com/oekit/logx` —— 日志门面

两者只共享 `o` / `kit` 两个元素。

## CI

全 org 共用一套门禁，规则本体在
[.github](https://github.com/oagentkit/.github) 仓的 `.github/workflows/go-ci.yml`
（`@main` 引用）。各模块仓的 `.github/workflows/ci.yml` 只是薄调用层，
**改门禁规则只改那一个仓，所有仓立即生效**。

门禁内容：`gofmt` / `go vet` / `golangci-lint` / `go test -race` / `govulncheck`。

## 贡献

见 [CONTRIBUTING](https://github.com/oagentkit/.github/blob/main/CONTRIBUTING.md)；
13 个业务模块仓更细的工程红线见各自根目录的 `AGENTS.md`，
本仓（`.github`）见 [AGENTS.md](https://github.com/oagentkit/.github/blob/main/AGENTS.md)。
