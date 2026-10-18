# 支持渠道 — oagentkit

## 我该去哪儿？

| 你想… | 去这里 |
|---|---|
| 报 bug / 提需求 | 目标模块仓的 issue（标题带 `[模块]` 前缀） |
| 跨模块 / 矩阵拓扑问题 | [matrix-tools](https://github.com/oagentkit/matrix-tools/issues) |
| 旧单仓路径迁移 | [agentkit/MIGRATION.md](https://github.com/oagentkit/agentkit/blob/main/MIGRATION.md)（45 条旧→新包路径映射，另有 1 个已退役包） |
| 查某个模块装哪版 | [versions.json](https://oagentkit.github.io/versions.json)（机器可读，CI 生成）或模块 README 安装行 |
| 查 API / 能力面 | <https://oagentkit.github.io/llms.txt> + 各仓 `go doc` / `AGENTS.md` |
| 安全漏洞 | 见 [SECURITY.md](SECURITY.md) —— **不要**开公开 issue |

## 期望

- oagentkit 是维护者业余时间驱动的开源项目；SLA 不承诺，但 issue 不会默默消失。
- 能给出 **可复现例子**（模块版本 + 最小代码）的 issue 处理最快。
- 提问前请先读该模块 `README.md` / `AGENTS.md` —— 答案常在文档里。

## 与 oekit 的关系

oagentkit 与 [oekit](https://github.com/oekit)（Open Easy Kit，Go 微服务工具链）是两套独立矩阵，
**不是同一个项目**。oagentkit 的部分依赖来自 oekit（`oekit/pkg`、`oekit/concurrency`、`oekit/logx`），
但模块清单、分层规则、CI 门禁、issue 通道全部独立。

问题请开在**对应矩阵**的仓里：oekit 的问题去 `oekit/*`，oagentkit 的问题来 `oagentkit/*`。
