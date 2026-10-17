# oagentkit

> **O**pen **E**asy **Kit** — 开源 · 易用 · 工具集合

Go 后端模块矩阵：**21 个独立发版的仓库**，从零依赖微工具库到 Hertz/Kitex 生产级生态。依赖严格自底向上，全矩阵无环。

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/oagentkit/oagentkit/main/docs/assets/architecture-dark.svg">
    <img src="https://raw.githubusercontent.com/oagentkit/oagentkit/main/docs/assets/architecture.svg" alt="oagentkit 模块矩阵 · 架构总览">
  </picture>
</p>

- 🌐 官方站点：**https://oagentkit.github.io/**（含 8 篇指南）
- 📦 模块矩阵：[pkg](https://github.com/oagentkit/pkg)（21 合 1 微工具集）· [logx](https://github.com/oagentkit/logx) · [tracing](https://github.com/oagentkit/tracing) · [registry](https://github.com/oagentkit/registry) · [middleware](https://github.com/oagentkit/middleware) · [hertz](https://github.com/oagentkit/hertz) · [kitex](https://github.com/oagentkit/kitex) · [bootstrap](https://github.com/oagentkit/bootstrap) · …
- 🏗️ 架构总览：[oagentkit/agentkit](https://github.com/oagentkit/agentkit#架构总览)（上图源文件 + 分层表 + 关键依赖链）
- 🤖 AI Agent：每仓 `AGENTS.md` + 全站 [llms.txt](https://oagentkit.github.io/llms.txt) + [《AI Agent 使用》](https://oagentkit.github.io/guide/ai-agent.html)
- 📜 沿革：源自 ekit 单体库（git.enjoye.top/enjoydream/ekit v0.34.0，冻结维护），2026-10 按框架耦合分层拆分

## 治理大盘

全 org 的进度/质量/版本不散落在 21+ 个仓里看——收在 4 块 [GitHub Projects](https://github.com/orgs/oagentkit/projects) 上（公开、实时）：

| 大盘 | 看什么 | 链接 |
|---|---|---|
| **Issue 大盘** | 跨仓缺陷/增强/重构/文档统一视图，Status × Kind × Repository | [projects/1](https://github.com/orgs/oagentkit/projects/1) |
| **版本大盘** | 21 模块最新 tag / 上一版 / 层级归属 / 嵌套模块 | [projects/2](https://github.com/orgs/oagentkit/projects/2) |
| **矩阵健康** | 安装行↔tag 对齐、门面壳/AGENTS/CHANGELOG 卫生巡检 | [projects/3](https://github.com/orgs/oagentkit/projects/3) |
| **PR 审查队列** | 跨仓 PR 统一进出，避免散落各仓被漏看 | [projects/4](https://github.com/orgs/oagentkit/projects/4) |

**联动约定**（细则见 [CONTRIBUTING](https://github.com/oagentkit/.github/blob/main/CONTRIBUTING.md)）：

1. 开 issue → 标题带 `[模块]` 前缀 → 进 **Issue 大盘**（开闭态自动映射 Todo/Done）
2. 提 PR → 在 **PR 审查队列** 入队，标 Scope（feat/fix/docs/…）
3. 发版 → 刷 **版本大盘** 的 Latest/Prior；安装行漂移会出现在 **矩阵健康**
4. `matrix-tools` 周巡检发现漂移 → 自动开 `matrix-drift` issue → 同样汇入 Issue 大盘

## 快速开始

```bash
go get github.com/oagentkit/pkg@latest        # 微工具集
go get github.com/oagentkit/logx@latest       # 日志门面
go get github.com/oagentkit/middleware@latest github.com/oagentkit/hertz@latest  # HTTP 全家
```

MIT License
