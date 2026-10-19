#!/usr/bin/env python3
"""oagentkit org 大盘（Project #1「oagentkit Issue 大盘」）统一同步。

Projects v2 不会自动跟进 org 的 issue/PR，本脚本补齐：

  #1 oagentkit Issue 大盘 — org 全仓 issue + PR → 板
                           Status = Todo / In Progress / Done
                           Kind   = 按标题信号打分（Bug/Enhancement/Refactor/Docs/Chore）
                           Module = 所属仓（真相源 = matrix-tools/modules.json）

用法：
  python3 scripts/sync-projects.py --apply                 # 全量同步
  python3 scripts/sync-projects.py                          # dry-run
  python3 scripts/sync-projects.py --link URL --state open   # 单条即时入板
  python3 scripts/sync-projects.py --refresh-module llm     # tag 推送后刷新单仓

环境：
  GH_TOKEN   需 project(读写) + read:org + repo
  ORG        默认 oagentkit
  PROJECT    看板号，默认 1

失败语义（不许静默通过）：
  · token 无 project 权限 / 看板读不到 / 某仓读不到 / 结果疑似被截断
    → 一律非零退出，workflow 红掉，并打印具体原因与涉及的仓。
  · 同步后做「逐条 URL 审计」：发现的每条 issue/PR 都必须在板上，
    缺一条就非零退出（防「N/M 仓被跳过、报告仍显示通过」那类门禁静默失效）。
  · 审计走的是 org API 读到的 URL 集合 ↔ 板上条目的 URL 集合，
    不复用 Kind/Module 推导逻辑——推导错了只是标签不准，不影响覆盖判定。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ORG = os.environ.get("ORG", "oagentkit")
PROJECT = int(os.environ.get("PROJECT", "1"))
MANIFEST_REPO = os.environ.get("MANIFEST_REPO", f"{ORG}/matrix-tools")
MANIFEST_PATH = os.environ.get("MANIFEST_PATH", "modules.json")
PAGE_LIMIT = int(os.environ.get("PAGE_LIMIT", "200"))

# 单选字段的选项配色轮转
COLOR_CYCLE = ["GRAY", "BLUE", "GREEN", "YELLOW", "ORANGE", "RED", "PINK", "PURPLE"]


class SyncError(RuntimeError):
    """硬失败：必须让 workflow 红掉，不能静默跳过。"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def gql(query: str, **variables: str) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        # Int! project number needs typed JSON; everything else stays string
        if k == "number" and str(v).isdigit():
            args += ["-F", f"{k}={v}"]
        else:
            args += ["-f", f"{k}={v}"]
    out = _run_gh(args, what="gh api graphql")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise SyncError(f"gh api graphql 返回非 JSON：{out[:200]}（{e}）")
    if "errors" in data:
        msgs = "; ".join(str(e.get("message", e)) for e in data["errors"])
        raise SyncError(f"GraphQL 报错：{msgs}")
    return data["data"]


def _is_transient(detail: str) -> bool:
    """判断 gh 的失败输出是不是**瞬时**错误（可重试）。

    实测（2026-10-20）：批量 GraphQL mutation 偶发 `gh: HTTP 502`，
    一次 502 就让整轮同步失败——但同步是**每小时**跑的，瞬时错误不该造成
    一次完整的空转（看板该更新的没更新，且下一次要等一小时）。

    只对**服务端/限流类**错误重试：5xx、429、超时、连接被重置。
    认证失败、权限不足、GraphQL 语义错误（errors 数组）一律**不重试**——
    那些重试多少次都一样，只会浪费时间并把真因埋掉。
    """
    d = (detail or "").lower()
    markers = (
        "http 500", "http 502", "http 503", "http 504", "http 429",
        "bad gateway", "service unavailable", "gateway timeout",
        "timeout", "timed out", "connection reset", "connection refused",
        "unexpected eof", "temporary failure", "rate limit",
    )
    return any(m in d for m in markers)


def _run_gh(args: list[str], *, what: str, retries: int = 3) -> str:
    """跑 gh，遇到瞬时错误按 2s/6s 退避重试；返回 stdout。

    非瞬时错误立刻抛 SyncError——**不吞错**：门禁要能指到真正的原因。
    """
    last = ""
    for attempt in range(retries):
        p = subprocess.run(["gh", *args], text=True, capture_output=True)
        if p.returncode == 0:
            return p.stdout
        last = (p.stderr or p.stdout).strip()
        if attempt == retries - 1 or not _is_transient(last):
            raise SyncError(f"{what} 失败：{last[:500]}")
        wait = 2 * (3 ** attempt)
        print(f"  · 瞬时错误（{last[:100]}），{wait}s 后重试（{attempt + 1}/{retries - 1}）")
        time.sleep(wait)
    raise SyncError(f"{what} 失败：{last[:500]}")  # pragma: no cover


def gql_batch(mutations: list[str], *, chunk: int = 20) -> list[dict]:
    """把多个 mutation 用别名合成一次 GraphQL 调用，减少请求数与二级限流风险。

    返回与 mutations 等长的结果列表；某个别名失败时该位为 None，
    由调用方逐个重试（重试仍失败则抛 SyncError，不吞错）。
    """
    results: list[dict | None] = []
    for start in range(0, len(mutations), chunk):
        group = mutations[start : start + chunk]
        parts = [f"  m{i}: {m}" for i, m in enumerate(group)]
        query = "mutation {\n" + "\n".join(parts) + "\n}"
        out = _run_gh(
            ["api", "graphql", "-f", f"query={query}"],
            what="批量 GraphQL",
        )
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise SyncError(f"批量 GraphQL 返回非 JSON：{out[:200]}（{e}）")
        payload = data.get("data") or {}
        for i in range(len(group)):
            results.append(payload.get(f"m{i}"))
    return results


def gh_json(argv: list[str], *, what: str = "") -> object:
    p = subprocess.run(["gh", *argv], text=True, capture_output=True)
    if p.returncode != 0:
        detail = (p.stderr or p.stdout).strip()
        raise SyncError(f"{what or ' '.join(argv[:3])} 读取失败：{detail[:400]}")
    out = p.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise SyncError(f"{what or ' '.join(argv[:3])} 返回非 JSON：{out[:200]}（{e}）")


def gh_text(argv: list[str], *, what: str = "") -> str:
    """跑 gh 并返回**原始文本**（不做 JSON 解析）。

    实测（2026-10-20）：`gh api .../contents/<path> --jq .content` 输出的是
    **裸 base64 文本**，不是 JSON。走 gh_json 会 json.loads(base64) 抛
    JSONDecodeError，被上层 `except SyncError: continue` 吞掉，最终报
    「模块清单读不到」——而这个错误完全指不到真正的原因（是解析方式错，
    不是清单不在）。

    更阴的是：本地跑时 `root/matrix-tools/modules.json` 存在，**永远走本地分支**，
    这条 API 路径只有 CI 才执行得到 → 「本地全绿、CI 必红」。
    所以取文件内容这类**非 JSON 输出**必须走 gh_text。
    """
    p = subprocess.run(["gh", *argv], text=True, capture_output=True)
    if p.returncode != 0:
        detail = (p.stderr or p.stdout).strip()
        raise SyncError(f"{what or ' '.join(argv[:3])} 读取失败：{detail[:400]}")
    return p.stdout.strip()


def esc(s: str) -> str:
    """GraphQL 字符串字面量转义。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def parse_github_url(url: str) -> tuple[str, int, str]:
    """→ (repo full name, number, kind) kind in {issues,pulls}"""
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", url)
    if not m:
        raise SyncError(f"无法解析 URL：{url}")
    kind = "issues" if m.group(2) == "issues" else "pulls"  # REST path
    return m.group(1), int(m.group(3)), kind


# ---------------------------------------------------------------------------
# 模块清单（真相源 = matrix-tools/modules.json）
# ---------------------------------------------------------------------------


def _read_manifest_text() -> tuple[str, str]:
    """返回 (modules.json 文本, 来源说明)。本地有就用本地，否则走 API。"""
    root = Path(__file__).resolve().parents[2]  # <org-root>/.github/scripts/..
    for cand in (
        root / "matrix-tools" / MANIFEST_PATH,
        root / "matrix-tools" / "scripts" / MANIFEST_PATH,
    ):
        if cand.exists():
            return cand.read_text(encoding="utf-8"), f"local {cand}"

    for path in (MANIFEST_PATH, f"scripts/{MANIFEST_PATH}"):
        try:
            # 走 gh_text 而非 gh_json：--jq .content 输出的是裸 base64，不是 JSON
            raw = gh_text(
                ["api", f"repos/{MANIFEST_REPO}/contents/{path}", "--jq", ".content"],
                what=f"模块清单 {MANIFEST_REPO}/{path}",
            )
        except SyncError:
            continue
        if not raw:
            continue
        text = base64.b64decode(str(raw)).decode("utf-8", "replace")
        return text, f"api {MANIFEST_REPO}/{path}"
    raise SyncError(
        f"模块清单读不到：{MANIFEST_REPO}/{MANIFEST_PATH}（本地也没有）。"
        "看板的 Module 列真相源必须是 modules.json，不允许手写清单。"
    )


def load_manifest() -> tuple[list[str], list[str], str]:
    """→ (模块仓, 基建仓, 来源说明)。modules.json 的 modules/infraRepos 是清单真相源。"""
    text, src = _read_manifest_text()
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise SyncError(f"模块清单不是合法 JSON（{src}）：{e}")
    mods = [m["repo"] for m in d.get("modules", []) if isinstance(m, dict) and m.get("repo")]
    infra = [r["repo"] for r in d.get("infraRepos", []) if isinstance(r, dict) and r.get("repo")]
    if not mods and not infra:
        raise SyncError(f"模块清单为空（{src}）：modules/infraRepos 都没有 repo 字段")
    return mods, infra, src


# ---------------------------------------------------------------------------
# project primitives
# ---------------------------------------------------------------------------


def load_project(number: int) -> dict:
    """拉全项目字段与条目（items 游标分页，避免 first:100 静默截断）。"""
    fields: dict[str, dict] = {}
    items: list[dict] = []
    pid = None
    cursor: str | None = None
    while True:
        vars_ = {"org": ORG, "number": str(number)}
        if cursor:
            vars_["cursor"] = cursor
        data = gql(
            """
            query($org: String!, $number: Int!, $cursor: String) {
              organization(login: $org) {
                projectV2(number: $number) {
                  id
                  title
                  fields(first: 50) {
                    nodes {
                      ... on ProjectV2Field { id name }
                      ... on ProjectV2SingleSelectField {
                        id name
                        options { id name color description }
                      }
                    }
                  }
                  items(first: 100, after: $cursor) {
                    pageInfo { hasNextPage endCursor }
                    nodes {
                      id
                      type
                      content {
                        ... on Issue { number url state title
                                       repository { nameWithOwner } }
                        ... on PullRequest { number url state title
                                             repository { nameWithOwner } }
                        ... on DraftIssue { title body }
                      }
                      fieldValues(first: 50) {
                        nodes {
                          ... on ProjectV2ItemFieldSingleSelectValue {
                            name
                            field {
                              ... on ProjectV2Field { id name }
                              ... on ProjectV2SingleSelectField { id name }
                            }
                          }
                          ... on ProjectV2ItemFieldTextValue {
                            text
                            field {
                              ... on ProjectV2Field { id name }
                              ... on ProjectV2SingleSelectField { id name }
                            }
                          }
                          ... on ProjectV2ItemFieldNumberValue {
                            number
                            field {
                              ... on ProjectV2Field { id name }
                              ... on ProjectV2SingleSelectField { id name }
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
            """,
            **vars_,
        )
        proj = data["organization"].get("projectV2")
        if proj is None:
            raise SyncError(
                f"看板读不到：org {ORG} 下不存在 Project #{number}"
                "（或 token 无 project 读权限）。"
            )
        pid = proj["id"]
        for f in proj["fields"]["nodes"]:
            fields[f["name"]] = f
        for n in proj["items"]["nodes"]:
            content = n.get("content") or {}
            fv = {}
            for x in n.get("fieldValues", {}).get("nodes", []):
                fname = (x.get("field") or {}).get("name")
                if not fname:
                    continue
                if "text" in x and x.get("text") is not None:
                    fv[fname] = x.get("text")
                elif "number" in x and x.get("number") is not None:
                    fv[fname] = x.get("number")
                else:
                    fv[fname] = x.get("name")
            items.append(
                {
                    "id": n["id"],
                    "type": n.get("type"),
                    "url": content.get("url"),
                    "title": content.get("title"),
                    "state": content.get("state"),
                    "repo": (content.get("repository") or {}).get("nameWithOwner"),
                    "number": content.get("number"),
                    "fv": fv,
                }
            )
        page = proj["items"]["pageInfo"]
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        if not cursor:
            break
    return {"id": pid, "number": number, "title": proj.get("title"), "fields": fields, "items": items}


def set_field(pid: str, item_id: str, field_id: str, *, option_id: str | None = None, text: str | None = None) -> None:
    if option_id is not None:
        value = f'{{ singleSelectOptionId: "{option_id}" }}'
    elif text is not None:
        value = f'{{ text: "{esc(text)}" }}'
    else:
        raise ValueError("no value")
    gql(
        f"""
        mutation {{
          updateProjectV2ItemFieldValue(input: {{
            projectId: "{pid}", itemId: "{item_id}", fieldId: "{field_id}",
            value: {value}
          }}) {{ projectV2Item {{ id }} }}
        }}
        """
    )


def add_url(pid: str, url: str) -> str:
    repo, num, kind = parse_github_url(url)
    node = subprocess.run(
        ["gh", "api", f"repos/{repo}/{kind}/{num}", "--jq", ".node_id"],
        text=True,
        capture_output=True,
    )
    if node.returncode != 0:
        raise SyncError(f"取不到条目 node_id：{url}（{(node.stderr or node.stdout).strip()[:200]}）")
    data = gql(
        f"""
        mutation {{
          addProjectV2ItemById(input: {{ projectId: "{pid}", contentId: "{node.stdout.strip()}" }}) {{
            item {{ id }}
          }}
        }}
        """
    )
    return data["addProjectV2ItemById"]["item"]["id"]


def resolve_node_ids(urls: list[str], *, chunk: int = 20) -> list[str | None]:
    """批量取 issue/PR 的 node_id（GraphQL 别名批查，避免逐条 REST）。"""
    out: list[str | None] = []
    specs: list[tuple[str, str, int, str]] = []
    for url in urls:
        repo, num, kind = parse_github_url(url)
        owner, name = repo.split("/", 1)
        field = "issue" if kind == "issues" else "pullRequest"
        specs.append((owner, name, num, field))
    for start in range(0, len(specs), chunk):
        group = specs[start : start + chunk]
        parts = [
            f'  r{i}: repository(owner: "{o}", name: "{n}") {{ {f}(number: {num}) {{ id }} }}'
            for i, (o, n, num, f) in enumerate(group)
        ]
        query = "query {\n" + "\n".join(parts) + "\n}"
        p = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            text=True,
            capture_output=True,
        )
        if p.returncode != 0:
            raise SyncError(f"批量取 node_id 失败：{(p.stderr or p.stdout).strip()[:400]}")
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise SyncError(f"批量取 node_id 返回非 JSON：{p.stdout[:200]}（{e}）")
        if "errors" in data:
            msgs = "; ".join(str(e.get("message", e)) for e in data["errors"])
            raise SyncError(f"批量取 node_id GraphQL 报错：{msgs}")
        payload = data.get("data") or {}
        for i in range(len(group)):
            node = (payload.get(f"r{i}") or {}).get("id")
            out.append(node)
    return out


def add_urls_batched(pid: str, urls: list[str]) -> list[str | None]:
    """批量入板。返回与 urls 等长的 item_id 列表（失败的位为 None）。"""
    nodes = resolve_node_ids(urls)
    muts = []
    for url, node in zip(urls, nodes):
        if not node:
            muts.append(None)  # 占位，下面跳过
            continue
        muts.append(
            f'addProjectV2ItemById(input: {{ projectId: "{pid}", contentId: "{node}" }}) {{ item {{ id }} }}'
        )
    live = [(i, m) for i, m in enumerate(muts) if m]
    res = gql_batch([m for _, m in live]) if live else []
    out: list[str | None] = [None] * len(urls)
    for (i, _), r in zip(live, res):
        out[i] = ((r or {}).get("addProjectV2ItemById") or {}).get("item", {}).get("id")
    return out


def set_fields_batched(pid: str, triples: list[tuple[str, str, str]]) -> list[bool]:
    """批量写单选字段。triples = [(item_id, field_id, option_id)]。返回逐条成功与否。"""
    muts = [
        f'updateProjectV2ItemFieldValue(input: {{ projectId: "{pid}", itemId: "{iid}", '
        f'fieldId: "{fid}", value: {{ singleSelectOptionId: "{oid}" }} }}) {{ projectV2Item {{ id }} }}'
        for iid, fid, oid in triples
    ]
    if not muts:
        return []
    res = gql_batch(muts, chunk=25)
    return [bool((r or {}).get("updateProjectV2ItemFieldValue")) for r in res]


def opt_map(field: dict | None) -> dict[str, str]:
    if not field:
        return {}
    return {o["name"]: o["id"] for o in field.get("options", [])}


# ---------------------------------------------------------------------------
# 看板字段自愈（Kind / Module 单选）
# ---------------------------------------------------------------------------


def _opt_literal(name: str, color: str, desc: str, oid: str | None = None) -> str:
    oid_part = f'id: "{oid}", ' if oid else ""
    return f'{{ {oid_part}name: "{esc(name)}", color: {color}, description: "{esc(desc)}" }}'


def ensure_select_field(proj: dict, name: str, wanted: list[tuple[str, str, str]], apply: bool) -> dict | None:
    """确保单选字段存在且含 wanted 选项；返回字段定义（dry-run 下可能为 None）。"""
    f = proj["fields"].get(name)
    if f is None:
        if not apply:
            print(f"  [dry-run] 将新建单选字段 {name}（{len(wanted)} 个选项）")
            return None
        opts = ", ".join(_opt_literal(n, c, d) for n, c, d in wanted)
        data = gql(
            f"""
            mutation {{
              createProjectV2Field(input: {{
                projectId: "{proj['id']}",
                name: "{esc(name)}",
                dataType: SINGLE_SELECT,
                singleSelectOptions: [{opts}]
              }}) {{
                projectV2Field {{
                  ... on ProjectV2SingleSelectField {{ id name options {{ id name color description }} }}
                }}
              }}
            }}
            """
        )
        fld = data["createProjectV2Field"]["projectV2Field"]
        proj["fields"][name] = fld
        print(f"  + 新建字段 {name}（{len(fld.get('options', []))} 个选项）")
        return fld

    have = {o["name"] for o in f.get("options", [])}
    missing = [w for w in wanted if w[0] not in have]
    if not missing:
        return f
    if not apply:
        print(f"  [dry-run] 将为字段 {name} 补选项：{[m[0] for m in missing]}")
        return f
    existing = f.get("options", [])
    opts = ", ".join(
        _opt_literal(o["name"], o.get("color", "GRAY"), o.get("description", ""), o["id"])
        for o in existing
    )
    opts += ", " + ", ".join(_opt_literal(n, c, d) for n, c, d in missing)
    data = gql(
        f"""
        mutation {{
          updateProjectV2Field(input: {{
            fieldId: "{f['id']}",
            singleSelectOptions: [{opts}]
          }}) {{
            projectV2Field {{
              ... on ProjectV2SingleSelectField {{ id name options {{ id name color description }} }}
            }}
          }}
        }}
        """
    )
    fld = data["updateProjectV2Field"]["projectV2Field"]
    proj["fields"][name] = fld
    print(f"  + 字段 {name} 补选项：{[m[0] for m in missing]}")
    return fld


# ---------------------------------------------------------------------------
# Kind 推导（标题信号打分）
# ---------------------------------------------------------------------------

# (pattern, kind, weight) —— 打分制，避免单关键词误伤
KIND_SIGNALS: list[tuple[re.Pattern[str], str, int]] = [
    # ---- Bug：已经坏了 / 不一致 / 漏判 ----
    (re.compile(r"fix\(|\[fix\]|\[bug\]", re.I), "Bug", 5),
    (
        re.compile(
            r"漏判|漏收|漏掉|漏检|漏了|未覆盖|不可达|崩溃|panic|越权|泄露|泄漏"
            r"|绕过|不一致|分歧|误判|误报|错误|陈旧|落后|失效|为空|不生效|差约",
            re.I,
        ),
        "Bug",
        3,
    ),
    # ---- Chore：矩阵卫生 / 依赖地板 / CI 配置 ----
    (
        re.compile(
            r"依赖地板|地板漂移|dep.?floor|\[matrix\]|矩阵卫生|CHANGELOG"
            r"|\[ci\]|GOPRIVATE|CI 配置",
            re.I,
        ),
        "Chore",
        4,
    ),
    # ---- Docs ----
    (
        re.compile(
            r"\[(docs?|文档|readme|llms|godoc)([/\]])|文档|llms\.txt|README"
            r"|CONTRIBUTING|godoc|未说明|注释",
            re.I,
        ),
        "Docs",
        4,
    ),
    # ---- Enhancement：缺能力 / 要新入口 ----
    (
        re.compile(
            r"\[(feature|feat)([/\]])|缺|需要|支持|提供|建议|增强|扩展|写死"
            r"|不可配|不可扩展|不可组合|无法复用|评估",
            re.I,
        ),
        "Enhancement",
        3,
    ),
    # ---- Refactor ----
    (re.compile(r"\[refactor\]|重构|拆分|拆出|归位|下线|收编|迁至", re.I), "Refactor", 3),
]


def infer_kind(title: str) -> str:
    """按信号打分归类；同分按 Bug > Enhancement > Refactor > Docs > Chore。"""
    scores = {"Bug": 0, "Enhancement": 0, "Refactor": 0, "Docs": 0, "Chore": 0}
    for pat, kind, weight in KIND_SIGNALS:
        if pat.search(title or ""):
            scores[kind] += weight
    if max(scores.values()) <= 0:
        return "Enhancement"
    for k in ("Bug", "Enhancement", "Refactor", "Docs", "Chore"):
        if scores[k] == max(scores.values()):
            return k
    return "Enhancement"


def infer_module(repo: str, title: str) -> str:
    """从 issue 所在仓推 Module；仓缺失时看标题前缀，再退回 meta。"""
    name = (repo or "").rstrip("/").split("/")[-1]
    if name:
        return name
    m = re.match(r"\[([^\]/]+)", title or "")
    if m:
        return m.group(1).strip()
    return "meta"


# ---------------------------------------------------------------------------
# 采集：逐仓读 issue / PR
# ---------------------------------------------------------------------------


def list_org_repos() -> list[str]:
    raw = gh_json(
        ["repo", "list", ORG, "--limit", "200", "--json", "name,isArchived,hasIssuesEnabled"],
        what=f"org {ORG} 仓列表",
    )
    if not raw:
        raise SyncError(f"org {ORG} 仓列表为空")
    if len(raw) >= 200:
        raise SyncError(f"org {ORG} 仓列表达到 200 上限，疑似被截断")
    live = [r["name"] for r in raw if not r.get("isArchived")]
    skipped = len(raw) - len(live)
    if skipped:
        print(f"  跳过 {skipped} 个已归档仓")
    return live


def gather_org_items(repos: list[str]) -> tuple[list[dict], list[str]]:
    """逐仓读 issue + PR。返回 (条目, 失败清单)。

    失败不吞：任何一仓读不到都记进失败清单，最后统一非零退出。
    """
    items: list[dict] = []
    failures: list[str] = []
    for repo in repos:
        try:
            issues = gh_json(
                [
                    "issue", "list", "-R", f"{ORG}/{repo}",
                    "--state", "all", "--limit", str(PAGE_LIMIT),
                    "--json", "number,title,state,url",
                ],
                what=f"{repo} 的 issue 列表",
            ) or []
        except SyncError as e:
            failures.append(str(e))
            continue
        if len(issues) >= PAGE_LIMIT:
            failures.append(f"{repo}: issue 数达到上限 {PAGE_LIMIT}，疑似被截断")
        for i in issues:
            items.append(
                {
                    "url": i["url"],
                    "repo": repo,
                    "number": i["number"],
                    "title": i["title"],
                    "state": i["state"],
                    "kind": "issue",
                }
            )

        try:
            prs = gh_json(
                [
                    "pr", "list", "-R", f"{ORG}/{repo}",
                    "--state", "all", "--limit", str(PAGE_LIMIT),
                    "--json", "number,title,state,url",
                ],
                what=f"{repo} 的 PR 列表",
            ) or []
        except SyncError as e:
            failures.append(str(e))
            continue
        if len(prs) >= PAGE_LIMIT:
            failures.append(f"{repo}: PR 数达到上限 {PAGE_LIMIT}，疑似被截断")
        for p in prs:
            items.append(
                {
                    "url": p["url"],
                    "repo": repo,
                    "number": p["number"],
                    "title": p["title"],
                    "state": p["state"],
                    "kind": "pr",
                }
            )
    return items, failures


# ---------------------------------------------------------------------------
# 同步
# ---------------------------------------------------------------------------


def want_status(item: dict) -> str:
    """issue: open→Todo, closed→Done；PR: open→In Progress, merged/closed→Done。"""
    st = (item["state"] or "").upper()
    if item["kind"] == "pr":
        return "In Progress" if st == "OPEN" else "Done"
    return "Todo" if st == "OPEN" else "Done"


def want_module(item: dict) -> str:
    return infer_module(item["repo"], item["title"])


def sync_board(apply: bool) -> int:
    print(f"\n=== org 大盘同步（Project #{PROJECT}）===")
    mods, infra, msrc = load_manifest()
    known = mods + infra
    print(f"  模块清单来源：{msrc}")
    print(f"  模块仓 {len(mods)} + 基建仓 {len(infra)} = {len(known)} 个")

    repos = list_org_repos()
    unknown = [r for r in repos if r not in known]
    if unknown:
        print(f"  ! 清单外的仓（按 meta 归类）：{unknown}")
    items, failures = gather_org_items(repos)
    n_issue = sum(1 for i in items if i["kind"] == "issue")
    n_pr = sum(1 for i in items if i["kind"] == "pr")
    print(f"  采集：{len(repos)} 仓 → issue {n_issue} + PR {n_pr} = {len(items)} 条")

    proj = load_project(PROJECT)
    print(f"  看板：{proj.get('title')}（现有 {len(proj['items'])} 项）")

    status_opts = opt_map(proj["fields"].get("Status"))
    for need in ("Todo", "Done", "In Progress"):
        if need not in status_opts:
            raise SyncError(f"看板缺少 Status 选项 {need}（现有：{sorted(status_opts)}）")
    status_id = proj["fields"]["Status"]["id"]

    kind_wanted = [
        ("Bug", "RED", "已坏的实现/漏判/不一致"),
        ("Enhancement", "BLUE", "缺能力/需扩展/新入口"),
        ("Refactor", "PURPLE", "结构重排/拆分/归位"),
        ("Docs", "GREEN", "文档/llms.txt/README"),
        ("Chore", "YELLOW", "矩阵卫生/依赖地板/CI 配置"),
    ]
    kind_field = ensure_select_field(proj, "Kind", kind_wanted, apply)
    kind_opts = opt_map(kind_field) if kind_field else {}
    kind_id = kind_field["id"] if kind_field else None

    module_wanted = [
        (r, COLOR_CYCLE[i % len(COLOR_CYCLE)], f"{r} 仓")
        for i, r in enumerate(known + ["meta"])
    ]
    module_field = ensure_select_field(proj, "Module", module_wanted, apply)
    module_opts = opt_map(module_field) if module_field else {}
    module_id = module_field["id"] if module_field else None

    by_url = {i["url"]: i for i in proj["items"] if i["url"]}
    adds: list[dict] = []
    syncs: list[dict] = []
    for it in items:
        st = want_status(it)
        kd = infer_kind(it["title"])
        md = want_module(it)
        if it["url"] not in by_url:
            adds.append({**it, "want_status": st, "want_kind": kd, "want_mod": md})
            continue
        item = by_url[it["url"]]
        cur_status = item["fv"].get("Status")
        cur_kind = item["fv"].get("Kind")
        cur_mod = item["fv"].get("Module")
        if cur_status != st or (kd and cur_kind != kd) or (md and cur_mod != md):
            syncs.append(
                {
                    "id": item["id"],
                    "url": it["url"],
                    "title": it["title"],
                    "status": st,
                    "kind": kd,
                    "mod": md,
                    "cur_status": cur_status,
                    "cur_kind": cur_kind,
                    "cur_mod": cur_mod,
                }
            )

    for a in adds:
        print(f"  + {a['url']}  {a['want_status']}/{a['want_kind']}/{a['want_mod']}")
    for s in syncs:
        bits = [f"status:{s['cur_status']}→{s['status']}"]
        if s["kind"] and s["kind"] != s["cur_kind"]:
            bits.append(f"kind:{s['cur_kind']}→{s['kind']}")
        if s["mod"] and s["mod"] != s["cur_mod"]:
            bits.append(f"mod:{s['cur_mod']}→{s['mod']}")
        print(f"  ~ {s['url']}  {'/'.join(bits)}")

    if not apply:
        print("  (dry-run，未落盘)")
        return 0

    # ---- 落盘：入板与字段写都走批量（GraphQL 别名批查），单条失败退化为逐个重试 ----
    pending: list[tuple[str, str, str | None, str | None]] = []
    if adds:
        iids = add_urls_batched(proj["id"], [a["url"] for a in adds])
        for a, iid in zip(adds, iids):
            if not iid:
                try:
                    iid = add_url(proj["id"], a["url"])
                except SyncError:
                    # 并发下可能已被另一次同步加进去，回看板确认而不是直接失败
                    cur = load_project(PROJECT)
                    hit = next((i for i in cur["items"] if i["url"] == a["url"]), None)
                    if not hit:
                        raise
                    iid = hit["id"]
            pending.append((iid, a["want_status"], a["want_kind"], a["want_mod"]))
    for s in syncs:
        pending.append((s["id"], s["status"], s["kind"], s["mod"]))

    triples: list[tuple[str, str, str]] = []
    for iid, status, kind, mod in pending:
        if status in status_opts:
            triples.append((iid, status_id, status_opts[status]))
        if kind and kind_id and kind in kind_opts:
            triples.append((iid, kind_id, kind_opts[kind]))
        if mod and module_id:
            if mod in module_opts:
                triples.append((iid, module_id, module_opts[mod]))
            elif "meta" in module_opts:
                triples.append((iid, module_id, module_opts["meta"]))
    oks = set_fields_batched(proj["id"], triples)
    failed = [t for t, good in zip(triples, oks) if not good]
    for iid, fid, oid in failed:
        # 批量里失败的逐个重试；再失败就抛 SyncError，不吞错
        set_field(proj["id"], iid, fid, option_id=oid)

    # ---- 同步后审计：每条采集到的条目都必须在板上 ----
    after = load_project(PROJECT)
    board_urls = {i["url"] for i in after["items"] if i["url"]}
    missing = [i["url"] for i in items if i["url"] not in board_urls]
    print(f"  审计：板上 {len(after['items'])} 项 / 采集 {len(items)} 条 → 缺 {len(missing)} 条")

    problems: list[str] = []
    if missing:
        problems.append("以下条目同步后仍不在板上：\n    " + "\n    ".join(missing[:50]))
    if failures:
        problems.append("以下仓读取失败（可能漏采）：\n    " + "\n    ".join(failures))
    if problems:
        raise SyncError("大盘同步未达预期：\n  - " + "\n  - ".join(problems))

    print(
        f"  OK：新增 {len(adds)} / 更新 {len(syncs)} / 板项 {len(after['items'])}"
        f"（issue {n_issue} + PR {n_pr}）"
    )
    return 0


# ---------------------------------------------------------------------------
# 即时入板（issue/PR 事件触发，单条快速路径）
# ---------------------------------------------------------------------------


def link_item(url: str, state: str) -> None:
    """把一条 issue/PR 立刻挂进大盘并写字段。state: open|closed|merged。"""
    repo, num, kind = parse_github_url(url)
    rest_kind = "issues" if kind == "issues" else "pulls"
    meta = gh_json(["api", f"repos/{repo}/{rest_kind}/{num}"], what=f"条目 {url}")
    title = (meta or {}).get("title") or ""
    real_state = (meta or {}).get("state") or state
    if kind == "pulls" and (meta or {}).get("merged"):
        real_state = "merged"

    proj = load_project(PROJECT)
    status_opts = opt_map(proj["fields"].get("Status"))
    status_id = proj["fields"]["Status"]["id"]
    kind_field = proj["fields"].get("Kind")
    module_field = proj["fields"].get("Module")
    kind_opts = opt_map(kind_field)
    module_opts = opt_map(module_field)

    item = next((i for i in proj["items"] if i["url"] == url), None)
    if not item:
        print(f"  + add {url}")
        iid = add_url(proj["id"], url)
    else:
        iid = item["id"]
        print(f"  = already on board {url}")

    st = (real_state or "").upper()
    if kind == "pulls":
        want = "In Progress" if st == "OPEN" else "Done"
    else:
        want = "Todo" if st == "OPEN" else "Done"
    want_kind = infer_kind(title)
    want_mod = infer_module(repo, title)

    if want in status_opts:
        set_field(proj["id"], iid, status_id, option_id=status_opts[want])
    if kind_field and want_kind in kind_opts:
        set_field(proj["id"], iid, kind_field["id"], option_id=kind_opts[want_kind])
    if module_field:
        if want_mod in module_opts:
            set_field(proj["id"], iid, module_field["id"], option_id=module_opts[want_mod])
        elif "meta" in module_opts:
            set_field(proj["id"], iid, module_field["id"], option_id=module_opts["meta"])
    print(f"  ✓ {kind}  status={want} kind={want_kind} mod={want_mod}  {title[:50]}")


# ---------------------------------------------------------------------------
# 单仓刷新（tag 推送后）
# ---------------------------------------------------------------------------


def latest_tag(repo: str) -> str:
    """该仓最新根 tag（版本真相只认 git tag，不看生成产物）。"""
    raw = gh_json(
        ["api", f"repos/{ORG}/{repo}/tags?per_page=100", "--jq", "[.[].name]"],
        what=f"{repo} 的 tag 列表",
    ) or []
    root = [t for t in raw if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not root:
        return ""
    key = lambda v: tuple(int(x) for x in v.lstrip("v").split("."))  # noqa: E731
    root.sort(key=key, reverse=True)
    return root[0]


def refresh_module(mod: str) -> None:
    """事件驱动的单仓刷新：重算该仓 issue/PR 的板面字段 + 打印最新 tag。

    oagentkit 目前只有 Project #1（Issue 大盘），没有版本大盘——
    这里显式声明「不产生版本行」，而不是静默跳过。
    """
    print(f"\n=== refresh {mod} ===")
    mods, infra, msrc = load_manifest()
    known = mods + infra
    if mod not in known:
        print(f"  警告：{mod} 不在模块清单（{msrc}）里，仍按其自身仓名归类")

    proj = load_project(PROJECT)
    status_opts = opt_map(proj["fields"].get("Status"))
    status_id = proj["fields"]["Status"]["id"]
    kind_field = proj["fields"].get("Kind")
    module_field = proj["fields"].get("Module")
    kind_opts = opt_map(kind_field)
    module_opts = opt_map(module_field)

    try:
        issues = gh_json(
            ["issue", "list", "-R", f"{ORG}/{mod}", "--state", "all",
             "--limit", str(PAGE_LIMIT), "--json", "number,title,state,url"],
            what=f"{mod} 的 issue 列表",
        ) or []
        prs = gh_json(
            ["pr", "list", "-R", f"{ORG}/{mod}", "--state", "all",
             "--limit", str(PAGE_LIMIT), "--json", "number,title,state,url"],
            what=f"{mod} 的 PR 列表",
        ) or []
    except SyncError as e:
        raise SyncError(f"刷新 {mod} 失败：{e}")

    for it in issues:
        it.update({"kind": "issue", "repo": mod})
    for it in prs:
        it.update({"kind": "pr", "repo": mod})

    by_url = {i["url"]: i for i in proj["items"] if i["url"]}
    for it in issues + prs:
        st = (it["state"] or "").upper()
        want = ("In Progress" if st == "OPEN" else "Done") if it["kind"] == "pr" else (
            "Todo" if st == "OPEN" else "Done"
        )
        want_kind = infer_kind(it["title"])
        want_mod = infer_module(mod, it["title"])
        item = by_url.get(it["url"])
        if not item:
            iid = add_url(proj["id"], it["url"])
            print(f"  + {it['url']}")
        else:
            iid = item["id"]
        if want in status_opts:
            set_field(proj["id"], iid, status_id, option_id=status_opts[want])
        if kind_field and want_kind in kind_opts:
            set_field(proj["id"], iid, kind_field["id"], option_id=kind_opts[want_kind])
        if module_field:
            if want_mod in module_opts:
                set_field(proj["id"], iid, module_field["id"], option_id=module_opts[want_mod])
            elif "meta" in module_opts:
                set_field(proj["id"], iid, module_field["id"], option_id=module_opts["meta"])

    tag = latest_tag(mod)
    print(f"  ✓ {mod}: issue {len(issues)} + PR {len(prs)} 条已对齐")
    print(f"  ✓ 最新 tag：{tag or '（无 vX.Y.Z 根 tag）'}")
    print(f"  · 版本大盘未配置（org 只有 Project #{PROJECT}），本仓不产生版本行——显式声明，非静默跳过")


# ---------------------------------------------------------------------------


def selftest() -> int:
    """自测：强制走 **API 路径** 取模块清单，并验证它是可解析的 JSON。

    为什么必须单独自测：`_read_manifest_text()` 在本地有 modules.json 时走本地分支，
    API 分支**只有 CI 才执行得到**。实测（2026-10-20）就栽在这里——API 分支用
    `gh_json` 解析 `--jq .content` 的裸 base64 输出，本地永远绿，CI 必红，
    且报错是「模块清单读不到」，完全指不到真正原因。

    所以：凡是「本地有文件就短路、只有 CI 才走到」的分支，都要有自测覆盖，
    否则它会一直悄悄烂着。CI 里必须跑本自测。
    """
    print("=== selftest：模块清单 API 路径 ===")
    # 1) API 取内容（base64 原文）
    raw = gh_text(
        ["api", f"repos/{MANIFEST_REPO}/contents/{MANIFEST_PATH}", "--jq", ".content"],
        what=f"模块清单 {MANIFEST_REPO}/{MANIFEST_PATH}",
    )
    if not raw:
        print("❌ API 返回空内容")
        return 1
    # 2) base64 解码后必须是合法 JSON
    try:
        text = base64.b64decode(raw).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"❌ base64 解码失败：{e}")
        return 1
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"❌ 解码后不是合法 JSON：{e}")
        return 1
    mods = data.get("modules")
    infra = data.get("infraRepos")
    if not isinstance(mods, list) or not mods:
        print("❌ modules 缺失或为空")
        return 1
    if not isinstance(infra, list) or not infra:
        print("❌ infraRepos 缺失或为空")
        return 1
    n_mod = sum(1 for m in mods if isinstance(m, dict) and m.get("repo"))
    print(f"✅ API 路径可用：modules {n_mod} 个 + infraRepos {len(infra)} 个")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正落盘（默认 dry-run）")
    ap.add_argument("--link", metavar="URL", help="即时把单条 issue/PR 挂进大盘")
    ap.add_argument("--state", default="open", help="--link 配套状态：open|closed|merged")
    ap.add_argument("--refresh-module", metavar="NAME", help="事件驱动：刷新单仓的板面字段")
    ap.add_argument("--selftest", action="store_true",
                    help="自测：强制走 API 路径取模块清单（CI 必须跑）")
    args = ap.parse_args()

    try:
        if args.selftest:
            return selftest()
        if args.link:
            link_item(args.link, args.state)
            print("\nOK")
            return 0
        if args.refresh_module:
            refresh_module(args.refresh_module)
            print("\nOK")
            return 0
        return sync_board(args.apply)
    except SyncError as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 —— 任何未预期异常也要让 workflow 红掉
        print(f"\nFAIL: 未预期异常 {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
