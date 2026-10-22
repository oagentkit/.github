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
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
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


# ---------------------------------------------------------------------------
# 看板读：可信度判据 + 重读（issue .github#22）
# ---------------------------------------------------------------------------
#
# 为什么需要：
#
# GitHub 的 GraphQL `projectV2.items` **会间歇性返回被截断的结果**——实测（2026-10-22）
# 同一个看板连读 6 次得到 479 / 100 / 400 / 400 / 200 / 100 项，而 `totalCount`
# **每次都是 479**；另一次读连 `totalCount` 本身都是 0。截断时 `hasNextPage`
# 会**提前变 false** ⇒ 光看 `hasNextPage` 察觉不到。
#
# ⇒ **读到空 / 读到少 ≠ 板上真的空 / 真的少**。
#
# 这个区别在这里是致命的，因为 `load_project()` 的读结果会被**两个**地方用：
#   ① 初始读（算 `by_url`，决定「哪些要入板、哪些要改字段」）；
#   ② 同步后的审计（算「板上缺哪些」）。
# 读被截断时：
#   ① 把**已经在板上的条目当成不存在** ⇒ 脚本去重复入板 ⇒ `add_url` 失败
#      ⇒ 同步链在**入板阶段**就炸（实测 2026-10-22 13:56Z 那次红就死在入板阶段，
#      不是死在审计）；
#   ② 把「没读到」当成「板上没有」⇒ 报「缺 N 条」——**假红**，
#      而真正的失败信号被淹没在里面。
#
# 所以：**读不全就重读；重读仍不全 ⇒ 显式报「取数失败」并中止**，
# 绝不把「读不可信」当成「事实如此」继续跑（判不了 ≠ 没问题）。
BOARD_READ_ATTEMPTS = 3
BOARD_READ_BACKOFF = 1.5
# 单页请求的重试：截断是**每请求**的（同一个 cursor 连打 10 次，8 次正常、
# 2 次退化成「0 条 + hasNextPage=false + totalCount=0」）⇒ 重试**同一个请求**
# 就能救回来，不必把整轮读推倒重来。
BOARD_PAGE_ATTEMPTS = 4
BOARD_PAGE_BACKOFF = 0.4


def _expected_total(total_counts: list[int | None]) -> int | None:
    """这一轮读到的**可信总数** → 首个非零的 totalCount（一个都没有则 None）。

    为什么取「首个非零」而不是「第一页那份」：
    - issue .github#22 建议 3：翻页途中某页读空时，它的 `totalCount=0`
      会覆盖真实总数 ⇒ 总数只认第一页那份。取「首个非零」同样挡住这种情况；
    - 实测还存在**第一页自己读空**（items 与 totalCount 同时为 0）⇒
      只认第一页会在这种读上永久失败。取「首个非零」能恢复。
    """
    for t in total_counts:
        if isinstance(t, int) and t > 0:
            return t
    return None


BOARD_ITEMS_QUERY = """
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
        totalCount
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
"""


def _fetch_page(vars_: dict, *, collected: int = 0,
                known_total: int | None = None) -> dict:
    """取**一页**看板；对**退化页**重试同一个请求。

    「退化页」= `nodes` 为空 **且** `totalCount` 读不到非零值
    ——服务端在这一请求上把整页返回成了空（实测同一个 cursor 连打 10 次，
    8 次正常、2 次退化成「0 条 + hasNextPage=false + totalCount=0」）。

    ⚠️ 为什么必须**逐页**重试而不是「整轮读重推」：截断是**每请求**发生的，
    同一个请求重打一次就能拿到完整页；把整轮读推倒重来会把前面几页也白读，
    且在慢网络下更慢（实测 CI 里整轮重试 4 次全灭）。

    重试仍不成功 ⇒ 原样返回最后一次的响应，由外层 `_board_read_verdict()`
    判它不可信（**不**在这里下结论）。
    """
    last_proj = None
    for attempt in range(1, BOARD_PAGE_ATTEMPTS + 1):
        data = gql(BOARD_ITEMS_QUERY, **vars_)
        proj = data["organization"].get("projectV2")
        if proj is None:
            raise SyncError(
                f"看板读不到：org {ORG} 下不存在 Project #{vars_.get('number')}"
                "（或 token 无 project 读权限）。"
            )
        last_proj = proj
        page = proj["items"]
        nodes = page.get("nodes") or []
        tc = page.get("totalCount")
        total_seen = tc if (isinstance(tc, int) and tc > 0) else known_total
        info = page.get("pageInfo") or {}
        # 退化页：既没有条目、又读不到非零的 totalCount
        degenerate = not nodes and total_seen is None
        # 短页：有条目、但 hasNextPage=false 且**总数对不上**
        #（服务端提前收尾：实测会在翻页途中把一页返回成空并置 hasNextPage=false）
        short = (
            not info.get("hasNextPage")
            and total_seen is not None
            and collected + len(nodes) < total_seen
        )
        if not degenerate and not short:
            return proj                      # 这一页可信 ⇒ 用它
        if attempt < BOARD_PAGE_ATTEMPTS:
            wait = BOARD_PAGE_BACKOFF * attempt
            why = "读空（退化页）" if degenerate else "提前收尾（短页）"
            print(f"  · 看板这一页{why}，{wait}s 后重打同一个请求"
                  f"（{attempt}/{BOARD_PAGE_ATTEMPTS}）")
            time.sleep(wait)
    return last_proj


def _load_project_once(number: int) -> dict:
    """读**一轮**看板 → 原始读数。

    **只取数，不判可信度**——判定在 `_board_read_verdict()`。
    取数与判定分开，是为了「读到了」和「读全了」不混为一谈。
    """
    fields: dict[str, dict] = {}
    items: list[dict] = []
    page_counts: list[int] = []         # 每一页读到的 node 数
    total_counts: list[int | None] = []  # 每一页读到的 totalCount（原样保留 0）
    pid = None
    title = None
    cursor: str | None = None
    while True:
        vars_ = {"org": ORG, "number": str(number)}
        if cursor:
            vars_["cursor"] = cursor
        proj = _fetch_page(vars_, collected=len(items),
                           known_total=_expected_total(total_counts))
        pid = proj["id"]
        title = proj.get("title")
        for f in proj["fields"]["nodes"]:
            fields[f["name"]] = f
        page = proj["items"]
        page_counts.append(len(page.get("nodes") or []))
        tc = page.get("totalCount")
        total_counts.append(tc if isinstance(tc, int) else None)
        for n in page.get("nodes") or []:
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
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
        if not cursor:
            break
    return {
        "id": pid,
        "number": number,
        "title": title,
        "fields": fields,
        "items": items,
        "page_counts": page_counts,
        "total_counts": total_counts,
        "total_count": _expected_total(total_counts),
    }


def _board_read_verdict(proj: dict) -> tuple[bool, str]:
    """这一轮看板读**可信不可信** → (可信, 理由)。

    三条都要满足：
    1. `totalCount` 至少读到一次**非零**值——读到 0 ⇒ 这次读不可信
       （实测存在 items 与 totalCount **同时为 0** 的读：那种读里
       `0 == 0` 会「通过」任何等值校验 ⇒ 绝不能当成读全了）；
    2. 所有**非零**的 `totalCount` 必须**相等**——不等 ⇒ 服务端返回不自洽；
    3. 读到的条数必须**等于** `totalCount`——少了就是被截断。
    """
    totals = [t for t in (proj.get("total_counts") or []) if isinstance(t, int) and t > 0]
    if not totals:
        return False, (
            "totalCount 一次都没读到非零值"
            "（实测存在 items 与 totalCount 同时为 0 的读——那是读空，不是板上是空的）"
        )
    expected = totals[0]
    if any(t != expected for t in totals):
        return False, f"各页读到的 totalCount 不一致：{totals}（服务端返回不自洽）"
    got = len(proj.get("items") or [])
    if got != expected:
        return False, (
            f"读到的条数 {got} ≠ totalCount {expected}"
            f"（读被截断；逐页读到 {proj.get('page_counts')}）"
        )
    return True, f"读到 {got} 项，与 totalCount 一致"


def _audit_split(missing: list[str], added_urls: list[str]) -> tuple[list[str], list[str]]:
    """审计判定 → (真的缺, 刚入板但看板索引还没同步的)。

    「刚入板」的那部分**不算缺**：mutation 返回了 item id ⇒ 条目确实在板上，
    只是看板的 items 连接是最终一致的、读里还看不到（见上面「审计」一节）。
    分开列出来是为了**显式记账**——不是静默放过。
    """
    just_added = set(added_urls)
    pending = sorted(set(missing) & just_added)
    return [u for u in missing if u not in just_added], pending


def load_project(number: int, *, attempts: int = BOARD_READ_ATTEMPTS) -> dict:
    """读看板 —— **读不全就重读；重读仍不全 ⇒ 显式报「取数失败」**。

    读的结果**只有可信时才交出去**：
    不可信的读绝不会被当成「板上就是空的 / 板上没有这些」——
    那会让审计报出「缺 N 条」的**假红**，也会让入板阶段去重复入板
    （把已经在板上的条目当成不存在）。

    取数失败时抛 SyncError，**中止**本次同步——「不许静默通过」这条纪律
    在这里指的是「不许把读不到当成事实」，而不是「读不到也要往下跑」。
    """
    reasons: list[str] = []
    for attempt in range(1, attempts + 1):
        proj = _load_project_once(number)
        ok, why = _board_read_verdict(proj)
        if ok:
            return proj
        reasons.append(f"第 {attempt}/{attempts} 轮：{why}")
        if attempt < attempts:
            wait = BOARD_READ_BACKOFF * attempt
            print(f"  · 第 {attempt}/{attempts} 轮读不可信（{why}），{wait}s 后重读")
            time.sleep(wait)
    raise SyncError(
        "看板取数失败（读到空 / 读到少 ≠ 板上真的空 / 真的少）：\n    "
        + "\n    ".join(reasons)
        + "\n  这是**取数问题**，不是「板上有而没读到」——本次**中止**，"
          "不同步、不审计（否则会把读空当成板上没有，报出假红）。"
    )


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
        for i, (_o, _n, _num, f) in enumerate(group):
            # ⚠️ id 在 alias 下的 issue/pullRequest 里（`r0: repository { issue { id } }`
            # → data.r0 = {"issue": {"id": ...}}），**不在 alias 直属**。
            # 旧写法 `(payload.get(f"r{i}") or {}).get("id")` 恒为 None
            # ⇒ 批量入板整批静默空转（只靠逐条 add_url 兜底，批量形同虚设）。
            node = ((payload.get(f"r{i}") or {}).get(f) or {}).get("id")
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
    added_urls: list[str] = []          # 本次**入板成功**的条目（审计时用）
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
            added_urls.append(a["url"])
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
    #
    # 这里有**两条**「读不到 ≠ 没有」，都必须处理（issue .github#22）：
    # ① 看板的 items 连接**间歇性**返回被截断/空的结果
    #    ⇒ `load_project()` 已做「校验 + 重读」，仍不可信就报取数失败；
    # ② 看板的 items 连接是**最终一致**的 ⇒ **刚入板**的条目在读里可能还看不到
    #    （实测：入板后 GraphQL 读里查不到，`totalCount` 也还是旧值）——
    #    这一条由**入板结果**裁决：mutation 返回了 item id ⇒ 条目确实在板上。
    #
    # ⇒ 顺序：先读一次；「读里没有」就**再读一次**排除①；仍没有的，
    #   再排除②（本次刚入板的）；剩下的才是**真的缺**。
    after = load_project(PROJECT)
    board_urls = {x["url"] for x in after["items"] if x["url"]}
    missing = [i["url"] for i in items if i["url"] not in board_urls]
    if missing:
        # 再读一次：排除「读被截断」
        time.sleep(BOARD_READ_BACKOFF)
        after = load_project(PROJECT)
        board_urls = {x["url"] for x in after["items"] if x["url"]}
        missing = [i["url"] for i in items if i["url"] not in board_urls]

    missing, pending_idx = _audit_split(missing, added_urls)
    print(
        f"  审计：板上 {len(after['items'])} 项"
        f"（totalCount {after.get('total_count')}，已对账）"
        f" / 采集 {len(items)} 条 → 缺 {len(missing)} 条"
        + (f"（另 {len(pending_idx)} 条本次刚入板，看板索引尚未同步，以入板结果为准）"
           if pending_idx else "")
    )

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


# ---------------------------------------------------------------------------
# 看板读的离线自测（issue .github#22）
# ---------------------------------------------------------------------------
#
# 为什么用**假 gh**而不是「等线上真的读空」：
# 线上读空是**间歇性**的（实测 6 次读里 4 次被截断），等它出现要碰运气，
# 而且它出现时 workflow 已经红了 —— 那样验证不了「脚本在该报取数失败的时候
# 确实报取数失败」。假 gh 能**确定性地**复刻每一种读坏掉的形态。
#
# 复刻的四种形态（都是实测见过的）：
#   ① 正常读：条数与 totalCount 一致；
#   ② 首次读空、第二次正常 ⇒ 应当**重读后采信**；
#   ③ 持续读空 ⇒ 应当报「取数失败」，**不是**「缺 N 条」；
#   ④ 截断读（条数 < totalCount）⇒ 同样报「取数失败」；
#   ⑤ 条数与 totalCount **同时为 0** ⇒ `0 == 0` 会「通过」任何等值校验
#      ⇒ 绝不能当成读全了（这一条是最容易被漏掉的那条）。
# 外加**反证样本**：把可信度判据短路成「永远可信」⇒ 上面 ③④⑤ 必须不再报错
# ⇒ 证明这些用例对「判据失效」有鉴别力，而不是「跑通即绿」的摆设。

FAKE_GH_SOURCE = r'''#!/usr/bin/env python3
"""假 gh：按**计划**依次返回 GraphQL 响应（复刻服务端间歇性读空 / 截断）。

计划文件是一个 JSON 数组：第 k 次调用返回第 k 项（超出则用最后一项）。
"""
import json
import os
import sys

plan = json.loads(open(os.environ["FAKE_GH_PLAN"], encoding="utf-8").read())
counter = os.environ["FAKE_GH_COUNTER"]
try:
    seen = int(open(counter, encoding="utf-8").read())
except Exception:
    seen = 0
with open(counter, "w", encoding="utf-8") as fh:
    fh.write(str(seen + 1))
sys.stdout.write(json.dumps(plan[min(seen, len(plan) - 1)]))
'''


def _fake_board_response(nodes: int, total: int, *,
                         has_next: bool = False, end_cursor: str | None = None) -> dict:
    """造一份 **GraphQL 看板读**的响应（形状与真实 API 一致）。

    真实响应里 `totalCount` 是看板总条数，`nodes` 是**这一页**读到的条目。
    实测服务端会返回**比 totalCount 少**的 nodes（被截断），这里能复刻那种形态。
    """
    items = []
    for i in range(nodes):
        num = 1000 + i
        items.append({
            "id": f"PVTI_{num}",
            "type": "ISSUE",
            "content": {
                "number": num,
                "url": f"https://github.com/oagentkit/llm/issues/{num}",
                "state": "CLOSED",
                "title": f"样本 issue {num}",
                "repository": {"nameWithOwner": "oagentkit/llm"},
            },
            "fieldValues": {"nodes": [
                {"name": "Done", "field": {"id": "F_status", "name": "Status"}},
            ]},
        })
    return {"data": {"organization": {"projectV2": {
        "id": "PVT_fake",
        "title": "oagentkit Issue 大盘",
        "fields": {"nodes": [
            {"id": "F_status", "name": "Status", "options": [
                {"id": "O_todo", "name": "Todo", "color": "GRAY", "description": ""},
                {"id": "O_done", "name": "Done", "color": "GREEN", "description": ""},
                {"id": "O_wip", "name": "In Progress", "color": "BLUE", "description": ""},
            ]},
        ]},
        "items": {
            "totalCount": total,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
            "nodes": items,
        },
    }}}}


def _fake_degenerate_page() -> dict:
    """造一份**退化页**响应：0 条 + totalCount=0 + hasNextPage=false。

    这就是实测里服务端偶发返回的那种页（同一个 cursor 连打 10 次，2 次退化成这样）。
    """
    return {"data": {"organization": {"projectV2": {
        "id": "PVT_fake",
        "title": "oagentkit Issue 大盘",
        "fields": {"nodes": [
            {"id": "F_status", "name": "Status", "options": [
                {"id": "O_todo", "name": "Todo", "color": "GRAY", "description": ""},
                {"id": "O_done", "name": "Done", "color": "GREEN", "description": ""},
                {"id": "O_wip", "name": "In Progress", "color": "BLUE", "description": ""},
            ]},
        ]},
        "items": {
            "totalCount": 0,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [],
        },
    }}}}


@contextlib.contextmanager
def _fake_gh(plan: list[dict]):
    """把 PATH 前置一个假 gh，让它按 plan 返回 GraphQL 响应。"""
    with tempfile.TemporaryDirectory(prefix="fake-gh-") as tmp:
        d = Path(tmp)
        script = d / "gh"
        script.write_text(FAKE_GH_SOURCE, encoding="utf-8")
        script.chmod(0o755)
        plan_file = d / "plan.json"
        plan_file.write_text(json.dumps(plan), encoding="utf-8")
        counter = d / "counter"
        counter.write_text("0", encoding="utf-8")
        saved = {k: os.environ.get(k) for k in ("PATH", "FAKE_GH_PLAN", "FAKE_GH_COUNTER")}
        os.environ["PATH"] = f"{d}{os.pathsep}{saved['PATH'] or ''}"
        os.environ["FAKE_GH_PLAN"] = str(plan_file)
        os.environ["FAKE_GH_COUNTER"] = str(counter)
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def selftest_board_read() -> int:
    """看板读的取数失败自测（离线；issue .github#22）。"""
    print("=== selftest：看板读的取数失败（issue .github#22）===")
    fails: list[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            fails.append(f"{name}: 期望 {want!r}，实得 {got!r}")

    def expect_ok(name: str, plan: list[dict]):
        """跑一次 load_project，期望它**成功**；失败要记成断言失败，
        而不是让异常把整个自测带崩（崩在异常上 = 用例没跑到断言）。"""
        try:
            with _fake_gh(plan):
                return load_project(1)
        except SyncError as e:
            fails.append(f"{name}: 期望成功返回，实得 SyncError：{str(e)[:160]}")
            return None

    def expect_sync_error(name: str, plan: list[dict]) -> str:
        """跑一次 load_project，期望它抛 SyncError；返回错误信息。"""
        try:
            with _fake_gh(plan):
                load_project(1)
        except SyncError as e:
            return str(e)
        fails.append(f"{name}: 期望抛 SyncError，实际没抛")
        return ""

    saved_backoff = globals()["BOARD_READ_BACKOFF"]
    globals()["BOARD_READ_BACKOFF"] = 0.0
    try:
        # ---- 1) 正常读：条数与 totalCount 一致 ⇒ 采信
        proj = expect_ok("正常读", [_fake_board_response(3, 3)])
        if proj is not None:
            check("正常读采信（条数）", len(proj["items"]), 3)
            check("正常读记录 totalCount", proj.get("total_count"), 3)

        # ---- 2) 首次读空、第二次正常 ⇒ 重读后采信（不是「板上就是空的」）
        proj = expect_ok("读空后重读",
                         [_fake_board_response(0, 3), _fake_board_response(3, 3)])
        if proj is not None:
            check("读空后重读采信（条数）", len(proj["items"]), 3)

        # ---- 3) 持续读空 ⇒ 报「取数失败」，**不是**「缺 N 条」
        msg = expect_sync_error("持续读空", [_fake_board_response(0, 3)])
        check("持续读空报取数失败", "取数失败" in msg, True)
        check("持续读空不报『→ 缺 N 条』", "→ 缺" in msg, False)

        # ---- 4) 截断读（条数 < totalCount）⇒ 同样报「取数失败」
        msg = expect_sync_error("截断读", [_fake_board_response(1, 3)])
        check("截断读报取数失败", "取数失败" in msg, True)
        check("截断读不报『→ 缺 N 条』", "→ 缺" in msg, False)

        # ---- 5) 条数与 totalCount 同时为 0 ⇒ 0 == 0 **不算读全了**
        msg = expect_sync_error("条数与总数同时为 0", [_fake_board_response(0, 0)])
        check("0==0 报取数失败", "取数失败" in msg, True)
        check("0==0 不报『→ 缺 N 条』", "→ 缺" in msg, False)

        # ---- 6) 各页 totalCount 不自洽 ⇒ 报「取数失败」
        msg = expect_sync_error(
            "各页 totalCount 不一致",
            [_fake_board_response(3, 3, has_next=True, end_cursor="cur1"),
             _fake_board_response(3, 5, has_next=False)],
        )
        check("各页 totalCount 不一致报取数失败", "取数失败" in msg, True)
        check("报出的是「不自洽」这个原因", "totalCount 不一致" in msg, True)

        # ---- 6a) 逐页重试：首���是**退化页** ⇒ 重打同一个请求就能救回来
        #           （实测截断是**每请求**的：同一 cursor 连打 10 次，8 次正常）
        proj = expect_ok("退化页重打",
                         [_fake_degenerate_page(), _fake_board_response(3, 3)])
        if proj is not None:
            check("退化页重打后采信（条数）", len(proj["items"]), 3)
            check("退化页重打后采信（totalCount）", proj.get("total_count"), 3)

        # ---- 6a-2) 逐页重试：**短页**（有条目但提前收尾）⇒ 重打同一个请求
        short_plan = [
            _fake_board_response(2, 4, has_next=True, end_cursor="cur1"),
            _fake_board_response(1, 0, has_next=False),        # 短页：1+2=3 < 4
            _fake_board_response(2, 4, has_next=False),        # 重打：2+2=4 ✓
        ]
        proj = expect_ok("短页重打", short_plan)
        if proj is not None:
            check("短页重打后采信（条数）", len(proj["items"]), 4)

        # ---- 6a-3) 每页都退化 ⇒ 仍然报「取数失败」（不把读空当板上是空的）
        msg = expect_sync_error("每页都退化", [_fake_degenerate_page()])
        check("每页都退化报取数失败", "取数失败" in msg, True)
        check("每页都退化不报『→ 缺 N 条』", "→ 缺" in msg, False)

        # ---- 6b) 审计判定：刚入板的条目**不算缺**（看板索引还没同步），
        #           真的缺的必须留在「缺」里
        missing, pending_idx = _audit_split(
            ["https://github.com/oagentkit/llm/issues/1",
             "https://github.com/oagentkit/llm/issues/2"],
            ["https://github.com/oagentkit/llm/issues/2"],
        )
        check("刚入板的不算缺", missing, ["https://github.com/oagentkit/llm/issues/1"])
        check("刚入板的单独记账", pending_idx, ["https://github.com/oagentkit/llm/issues/2"])

        # ---- 6c) resolve_node_ids：id 在 alias 下的 issue/pullRequest 里
        #           （旧写法恒为 None ⇒ 批量入板整批静默空转）
        with _fake_gh([{"data": {"r0": {"issue": {"id": "I_fake"}}}}]):
            got = resolve_node_ids(["https://github.com/oagentkit/llm/issues/1"])
        check("resolve_node_ids 取到嵌套的 id", got, ["I_fake"])
        with _fake_gh([{"data": {"r0": {"pullRequest": {"id": "PR_fake"}}}}]):
            got = resolve_node_ids(["https://github.com/oagentkit/llm/pull/1"])
        check("resolve_node_ids 取到 PR 的 id", got, ["PR_fake"])

        # ---- 7) 反证样本：把可信度判据短路成「永远可信」⇒ ③④⑤ 必须不再报错
        #         （否则这些用例对「判据失效」没有鉴别力）
        orig_verdict = globals()["_board_read_verdict"]
        try:
            globals()["_board_read_verdict"] = lambda proj: (True, "短路：永远可信")
            for label, plan in (
                ("③持续读空", [_fake_board_response(0, 3)]),
                ("④截断读", [_fake_board_response(1, 3)]),
                ("⑤0==0", [_fake_board_response(0, 0)]),
            ):
                try:
                    with _fake_gh(plan):
                        load_project(1)
                except SyncError:
                    fails.append(
                        f"反证：判据短路后「{label}」仍报错 ⇒ 用例对判据失效没有鉴别力"
                    )
        finally:
            globals()["_board_read_verdict"] = orig_verdict
    finally:
        globals()["BOARD_READ_BACKOFF"] = saved_backoff

    if fails:
        print("❌ 看板读自测失败：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ 看板读自测通过"
          "（正常读采信 / 读空后重读 / 持续读空报取数失败 / 截断读被拒 / "
          "0==0 不算读全 / 各页总数不自洽被拒 / 反证样本有鉴别力）")
    return 0


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

    # ---- 看板读的取数失败自测（离线，不依赖网络）----
    return selftest_board_read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正落盘（默认 dry-run）")
    ap.add_argument("--link", metavar="URL", help="即时把单条 issue/PR 挂进大盘")
    ap.add_argument("--state", default="open", help="--link 配套状态：open|closed|merged")
    ap.add_argument("--refresh-module", metavar="NAME", help="事件驱动：刷新单仓的板面字段")
    ap.add_argument("--selftest", action="store_true",
                    help="自测：强制走 API 路径取模块清单（CI 必须跑）")
    ap.add_argument("--selftest-board", action="store_true",
                    help="自测：看板读的取数失败（离线，不联网、不需要 token）")
    args = ap.parse_args()

    try:
        if args.selftest:
            return selftest()
        if args.selftest_board:
            return selftest_board_read()
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
