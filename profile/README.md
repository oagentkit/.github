# oagentkit

> **O**pen **A**gent **K**it — 开源 · 易用 · Agent 工具集合

**13 个独立发版的 Go 模块矩阵**，覆盖 Agent / LLM 编排工具链：模型客户端、MCP 工具池、
技能库、知识检索、Agent 编排、运行时基础设施、安全围栏、DB 队列。
依赖严格自底向上，全矩阵无环。

- 📦 **模块矩阵**（按 tier 自底向上）：

  | tier | 模块 |
  |---|---|
  | 0 | [textx](https://github.com/oagentkit/textx) · [runtime](https://github.com/oagentkit/runtime) · [guardx](https://github.com/oagentkit/guardx) · [queue](https://github.com/oagentkit/queue) |
  | 1 | [promptx](https://github.com/oagentkit/promptx) · [obs](https://github.com/oagentkit/obs) · [skillkit](https://github.com/oagentkit/skillkit) · [mcpkit](https://github.com/oagentkit/mcpkit) · [rag](https://github.com/oagentkit/rag) |
  | 2 | [llm](https://github.com/oagentkit/llm) · [toolgate](https://github.com/oagentkit/toolgate) · [tools](https://github.com/oagentkit/tools) |
  | 3 | [agent](https://github.com/oagentkit/agent) |

- 🤖 **AI Agent 入口**：每仓根目录 `AGENTS.md`（权威规范与硬规则）·
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
tier0:  textx   runtime   guardx   queue
           │       │        │        │
tier1:  promptx  obs   skillkit  mcpkit   rag
           │       │        │        │
tier2:   llm ◄───┘        │        ├──► toolgate
           │               │        │
tier3:  agent               └──► tools
```

tier N 的模块不得依赖 tier > N 的模块 —— 这条不变量由
`matrix-tools/scripts/verify_split.py` 在每次拆分/发版时强制校验。

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
单仓更细的工程红线见各仓的 `AGENTS.md`。
