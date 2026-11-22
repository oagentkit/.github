#!/usr/bin/env python3
"""org profile 的分层表与矩阵单一数据源的一致性门禁。

## 守的不变量

`profile/README.md` 是 org 的门面页（渲染在 github.com/oagentkit），是**人类与 AI Agent
的第一入口**。它写的 tier 分层表必须与 `matrix-tools/modules.json`（矩阵单一数据源）
**逐项一致**——门面页给错分层，AI Agent 就会按错的层位去选依赖。

## 为什么需要这条门禁

实测 2026-10-21：profile 的 tier 表与 modules.json 在 **5 个仓**上冲突——

| 仓 | profile 写的 | modules.json 的真实值 |
|---|---|---|
| queue | tier0 | **tier2** |
| obs | tier1 | **tier2** |
| skillkit | tier1 | **tier2** |
| mcpkit | tier1 | **tier2** |
| rag | tier1 | **tier2** |

旁证：矩阵门禁 `verify_go_floor.py` 实跑输出「tier0 = guardx, runtime, textx」——**只有 3 个**，
而 profile 声称 tier0 有 4 个（含 queue）；queue 实际 require `github.com/lib/pq`（DB 驱动），
本就不该在 tier0。当时**没有任何门禁会报出来**：CI 全绿，门面页给的是错的分层。

## 判据

只比「仓的集合」，不比文案：

1. 每个 tier 的仓集合 == modules.json 里该 tier 的仓集合（**双向**：少一个、多一个都判红）；
2. profile 声明的模块总数 == modules.json 的模块数；
3. profile 的 tier 表必须落在生成区标记之间（标记被删 = 判红，
   否则手改就能绕过门禁）。

## 生成区

profile 的 tier 表被 `<!-- oagentkit:generated:begin -->` / `<!-- oagentkit:generated:end -->`
包住，与 `matrix-tools/scripts/gen_docs.py` 用的是同一套标记约定。

本脚本默认**只校验不改文件**；`--write` 用数据源重写生成区（供本地修复用）。

## 用法

    python3 scripts/verify_profile_tiers.py \
        --matrix ../matrix-tools/modules.json \
        --profile profile/README.md

    python3 scripts/verify_profile_tiers.py --write   # 按数据源重写生成区

退出码：0 = 一致；1 = 漂移（打印逐行 diff 与修法）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

BEGIN = "<!-- oagentkit:generated:begin -->"
END = "<!-- oagentkit:generated:end -->"

ORG_URL = "https://github.com/oagentkit"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

DEFAULT_MATRIX = REPO_ROOT.parent / "matrix-tools" / "modules.json"
DEFAULT_PROFILE = REPO_ROOT / "profile" / "README.md"
DEFAULT_CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"


def load_modules(path: Path) -> list[dict]:
    if not path.is_file():
        print(f"❌ 找不到矩阵数据源：{path}")
        raise SystemExit(1)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"❌ {path} 不是合法 JSON：{e}")
        raise SystemExit(1)
    mods = data.get("modules") or []
    if not mods:
        print(f"❌ {path} 里没有 modules")
        raise SystemExit(1)
    return mods


def build_table(modules: list[dict], indent: str = "  ") -> str:
    """由数据源生成分层表（tier 升序，同 tier 内按仓名字母序）。

    `indent` 是表格每行的前缀——profile 里这张表挂在列表项下（缩进 2 空格），
    生成物必须带同样的缩进，否则比对的是排版而不是分层。
    """
    by_tier: dict[int, list[str]] = {}
    for m in modules:
        by_tier.setdefault(int(m["tier"]), []).append(str(m["repo"]))
    lines = ["| tier | 模块 |", "|---|---|"]
    for tier in sorted(by_tier):
        cells = " · ".join(
            f"[{repo}]({ORG_URL}/{repo})" for repo in sorted(by_tier[tier])
        )
        lines.append(f"| {tier} | {cells} |")
    return "\n".join(indent + line for line in lines)


def split_region(text: str) -> tuple[int, int]:
    """返回生成区内容在 text 中的 (起, 止) 偏移；标记缺失时退出。"""
    i = text.find(BEGIN)
    j = text.find(END)
    if i < 0 or j < 0:
        print("❌ profile 里找不到生成区标记：")
        print(f"   需要 {BEGIN}")
        print(f"   需要 {END}")
        print("   修法：把 tier 表放回这对标记之间（本脚本只管标记之间的内容，"
              "标记之外的手写段落永不触碰）。")
        raise SystemExit(1)
    if j < i:
        print("❌ 生成区标记顺序反了（end 在 begin 之前）")
        raise SystemExit(1)
    return i + len(BEGIN), j


def parse_contributing_table(text: str) -> dict[int, set[str]] | None:
    """CONTRIBUTING.md 的 tier 表 → {tier: {仓名}}。

    ## 为什么单独解析

    这张表与 profile 的生成区**形态不同**（profile 用 markdown 链接 + · 分隔，
    CONTRIBUTING 用反引号 + 空格分隔）⇒ 解析口径不同，但比对的是同一件事：
    **每个 tier 的仓集合**。

    ## 为什么它也得有门禁

    CONTRIBUTING.md 原文写着「上表与 matrix-tools/modules.json 逐仓一致，由本仓
    scripts/verify_profile_tiers.py 在 CI 里比对」——而**本脚本此前只查 profile**：
    承诺是假的（实测 2026-11-23 顺带发现：那张表当时也确实少一个仓）。
    ⇒ 要么加守卫，要么改文案；本脚本选择加守卫（把承诺改成真的）。
    """
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^\|\s*tier\s*\|", ln):
            start = i
            break
    if start is None:
        return None
    out: dict[int, set[str]] = {}
    for ln in lines[start + 1:]:
        if not ln.strip().startswith("|"):
            break
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        try:
            tier = int(cells[0])
        except ValueError:
            continue
        names = set()
        for token in re.split(r"[\s、,，·]+", cells[1]):
            token = token.strip().strip(chr(96))
            if token:
                names.add(token)
        out[tier] = names
    return out


def check_contributing(text: str, modules: list[dict]) -> list[str]:
    """CONTRIBUTING.md 的 tier 表必须与数据源逐 tier 一致（双向：缺一多一都判红）。"""
    got = parse_contributing_table(text)
    if got is None:
        return ["CONTRIBUTING.md 里找不到 tier 表（表头应是 | tier | 模块 |）"]
    want: dict[int, set[str]] = {}
    for m in modules:
        want.setdefault(int(m.get("tier", 0)), set()).add(m["repo"])
    problems: list[str] = []
    for tier in sorted(set(got) | set(want)):
        g, w = got.get(tier, set()), want.get(tier, set())
        if g == w:
            continue
        detail = []
        if w - g:
            detail.append("缺 " + ", ".join(sorted(w - g)))
        if g - w:
            detail.append("多 " + ", ".join(sorted(g - w)))
        problems.append("CONTRIBUTING.md 的 tier " + str(tier) + " 表与数据源不一致："
                        + "；".join(detail))
    return problems


def check_count(text: str, modules: list[dict]) -> list[str]:
    """profile 里声明的模块总数必须等于数据源的模块数。"""
    problems = []
    want = len(modules)
    for m in re.finditer(r"(\d+)\s*个独立发版的\s*Go\s*模块矩阵", text):
        got = int(m.group(1))
        if got != want:
            problems.append(
                f"profile 声明「{got} 个独立发版的 Go 模块矩阵」，数据源是 {want} 个"
            )
    return problems


def normalize(block: str) -> str:
    """归一化一段 markdown：逐行去尾空白、丢空行，**保留行首缩进**。

    行首缩进必须保留——profile 的 tier 表挂在列表项下（缩进 2 空格），
    用 `strip()` 会把第一行的缩进吃掉，比对的就变成排版而不是分层。
    """
    lines = [ln.rstrip() for ln in block.strip("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def main() -> int:
    ap = argparse.ArgumentParser(
        description="profile 分层表与 matrix-tools/modules.json 的一致性门禁")
    ap.add_argument("--matrix", default=str(DEFAULT_MATRIX),
                    help="矩阵单一数据源 modules.json 路径")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE),
                    help="org profile 文件路径")
    ap.add_argument("--contributing", default=str(DEFAULT_CONTRIBUTING),
                    help="CONTRIBUTING.md 路径（它的 tier 表同样要逐项比对）")
    ap.add_argument("--write", action="store_true",
                    help="按数据源重写 profile 的生成区（默认只校验）")
    args = ap.parse_args()

    matrix_path = Path(args.matrix).resolve()
    profile_path = Path(args.profile).resolve()

    modules = load_modules(matrix_path)
    expected = build_table(modules)

    if not profile_path.is_file():
        print(f"❌ 找不到 profile：{profile_path}")
        return 1
    text = profile_path.read_text(encoding="utf-8")

    start, end = split_region(text)
    current = normalize(text[start:end])

    if args.write:
        new_text = text[:start] + "\n" + expected + "\n" + text[end:]
        if new_text != text:
            profile_path.write_text(new_text, encoding="utf-8")
            print(f"✅ 已按 {matrix_path.name} 重写 {profile_path.name} 的生成区")
        else:
            print(f"✅ {profile_path.name} 的生成区已是最新")
        current = normalize(expected)

    problems = []
    if current != expected:
        diff = list(difflib.unified_diff(
            current.splitlines(), expected.splitlines(),
            fromfile="profile/README.md（当前）", tofile=f"{matrix_path.name}（数据源）",
            lineterm=""))
        print("❌ profile 的 tier 表与矩阵数据源漂移：\n")
        print("\n".join(diff))
        problems.append("tier 表与数据源不一致")

    for p in check_count(text, modules):
        print(f"❌ {p}")
        problems.append(p)

    # CONTRIBUTING.md 的 tier 表：文档写着「由本脚本在 CI 里比对」，
    # 那这条比对就必须真的存在（此前只查 profile ⇒ 承诺是假的）。
    contributing_path = Path(args.contributing).resolve()
    if contributing_path.is_file():
        ctext = contributing_path.read_text(encoding="utf-8")
        for p in check_contributing(ctext, modules):
            print(f"❌ {p}")
            problems.append(p)
    else:
        print(f"⚠️ 找不到 CONTRIBUTING.md（{contributing_path}）——"
              f"它的 tier 表本次未被比对")

    if problems:
        print("\n修法：")
        print("  1) 改 matrix-tools/modules.json（分层是数据源说了算，不要手改 profile）；")
        print("  2) 然后在本仓跑：python3 scripts/verify_profile_tiers.py --write")
        return 1

    print(f"✅ profile 与 CONTRIBUTING 的分层表都与 {matrix_path.name} 一致"
          f"（{len(modules)} 个仓，逐 tier 逐仓比对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
