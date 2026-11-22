#!/usr/bin/env bash
# 把 oagentkit org 各模块仓库的 .github/workflows/ci.yml 重写为
# 共享 reusable workflow（oagentkit/.github/.github/workflows/go-ci.yml）的薄调用层。
#
# 用法:
#   ./sync-callers.sh <repo-dir>...        # 改写后**开分支提 PR**（默认，不碰 main）
#   ./sync-callers.sh --push <repo-dir>...  # 直接改写并推送 main（**需显式声明**）
#
# 每仓的 go-version 从现存 ci.yml 读取（保持各仓节奏）；覆盖率/测试命令特例
# 写在下方 OVERRIDES。嵌套子模块由共享 workflow 的 test-nested 自动发现测试，
# 无需写 additional-script。规则本体变更请改 oagentkit/.github 仓，不需要重跑本脚本。
set -euo pipefail

REF=main # 引用共享 workflow 的 ref；要锁定稳定性可改为 tag（如 v1）
BRANCH=ci/shared-workflows

# ⚠️ **默认不直推 main**（2026-11-23 改）。
#
# 此前默认模式是「直推 main」⇒ 一次误用就把整文件重写直接推到主干。
# 实录：本轮用它给 textx 加 tag 触发时，模板与真实调用层已漂移（缺 concurrency
# 块与说明注释、branches 写 [main, master] 而真实是 [main]）⇒ 重写把调用层里的
# 并发块与注释**静默删掉**，且提交说明与实际改动不符 ⇒ 只能再补一个 revert。
#
# ⇒ 现在：默认开分支提 PR；真要直推 main 必须显式写 --push（意图外显）。
MODE=pr
if [ "${1:-}" = "--push" ]; then MODE=push; shift; fi
[ $# -ge 1 ] || { echo "usage: $0 [--push] <repo-dir>... | --selftest [ref-ci.yml]" >&2; exit 1; }

# 按仓覆盖（macOS bash 3.2 兼容：不用 declare -A）。
# 未命中的仓用共享默认：覆盖率门禁 30、标准测试命令、test-nested 自动开。
# oagentkit 全矩阵当前无需任何特例——此函数保留是为了将来某个仓要调覆盖率阈值或
# 关闭 test-nested 时有地方落，而不是临时改共享 workflow。
get_override() { # $1=repo basename → OV_THR/OV_TSCRIPT/OV_ASCRIPT/OV_NESTED（空串=共享默认）
  OV_THR=''; OV_TSCRIPT=''; OV_ASCRIPT=''; OV_NESTED=''
}

render_caller() { # $1=go_version $2=threshold $3=test_script $4=additional_script $5=test_nested
  local gover="$1" thr="$2" tscript="$3" ascript="$4" nested="$5"
  cat <<EOF
name: CI

# 薄调用层：门禁规则本体在 oagentkit/.github/.github/workflows/go-ci.yml。
# 改门禁规则只改那一个仓，这里不用动。

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ci-\${{ github.workflow }}-\${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  ci:
    name: Lint & Test
    uses: oagentkit/.github/.github/workflows/go-ci.yml@${REF}
    with:
EOF
  echo "      go-version: '${gover}'"
  [ -n "$thr" ] && [ "$thr" != 30 ] && echo "      coverage-threshold: $thr"
  [ -n "$nested" ] && [ "$nested" = false ] && echo "      test-nested: false"
  if [ -n "$tscript" ]; then
    echo "      test-script: |-"
    printf '%s\n' "$tscript" | sed 's/^/        /'
  fi
  if [ -n "$ascript" ]; then
    echo "      additional-script: |-"
    printf '%s\n' "$ascript" | sed 's/^/        /'
  fi
}

# ── 模板漂移自检（2026-11-23 加）────────────────────────────────────────
#
# 为什么要有：模板与真实调用层曾漂移（缺 concurrency 块与说明注释、
# branches 写 [main, master] 而真实是 [main]）⇒ 跑一次同步器就把调用层里的
# 并发块与注释**静默删掉**，而 diff 里看不出「少了东西」——它看起来像正常的重写。
#
# ⇒ 渲染必须与真实调用层**逐字节一致**才允许写盘；不一致 ⇒ 跳过该仓并报出，
# 绝不「以模板为准」覆盖掉调用层里模板没有的内容。
#
# 参考实现取自矩阵内任一已对齐的仓（textx 的调用层即基准）。
selftest_render() {
  local base="${1:-}"
  [ -n "$base" ] || { echo "selftest: 需要给出参考调用层路径" >&2; return 2; }
  [ -f "$base" ] || { echo "selftest: 参考文件不存在：$base" >&2; return 2; }
  local tmp
  tmp=$(mktemp)
  render_caller 1.26 '' '' '' '' > "$tmp"
  if diff -u "$base" "$tmp" > /dev/null; then
    echo "selftest: ✅ 渲染与参考调用层逐字节一致（$base）"
    rm -f "$tmp"
    return 0
  fi
  echo "selftest: ❌ 渲染与参考调用层不一致（$base）——差异如下：" >&2
  diff -u "$base" "$tmp" >&2 || true
  rm -f "$tmp"
  return 1
}

sync_repo() {
  local dir="$1" base
  base=$(basename "$dir")
  local ci="$dir/.github/workflows/ci.yml"
  [ -f "$ci" ] || { echo "skip $base: 无 ci.yml"; return 0; }

  git -C "$dir" diff --quiet -- .github/workflows/ci.yml ||
    { echo "skip $base: ci.yml 有未提交改动"; return 0; }

  local gover thr tscript ascript nested
  # 行形如 `go-version: '1.26'`；按引号切分取值（BSD sed 不支持 \?，勿用 sed BRE）
  gover=$(grep -m1 "go-version:" "$ci" | awk -F"'" '{print $2}')
  get_override "$base"
  if [ -n "$OV_THR" ] || [ -n "$OV_TSCRIPT" ] || [ -n "$OV_ASCRIPT" ] || [ -n "$OV_NESTED" ]; then
    thr="${OV_THR:-30}"; tscript="$OV_TSCRIPT"; ascript="$OV_ASCRIPT"; nested="$OV_NESTED"
  elif grep -q 'coverprofile' "$ci"; then
    thr=$(grep -o 'THRESHOLD=[0-9]*' "$ci" | head -1 | cut -d= -f2)
    thr=${thr:-30}; tscript=''; ascript=''; nested=''
  elif grep -q 'go-ci.yml' "$ci"; then
    # 已是薄调用层：保留 go-version，清掉历史 additional-script（test-nested 接管嵌套）
    thr=''; tscript=''; ascript=''; nested=''
  else
    echo "skip $base: 无覆盖率步骤且无 override，需人工确认"; return 0
  fi

  # PR 模式先开分支再改写提交，保证 commit 不落在本地 main 上
  if [ "$MODE" = pr ]; then
    git -C "$dir" checkout -q -B "$BRANCH"
  fi

  # 写盘前先核对：渲染结果必须与**当前调用层**兼容。
  # 判据不是「渲染 == 现有文件」（现有文件本来就是待替换的对象），而是
  # 「渲染出来的**结构骨架**在现有调用层里都存在」——模板若少了并发块/注释，
  # 写盘就会把它们删掉 ⇒ 这里逐项核对，缺一项就跳过该仓。
  local rendered
  rendered=$(mktemp)
  render_caller "$gover" "$thr" "$tscript" "$ascript" "$nested" > "$rendered"
  local key
  for key in "concurrency:" "cancel-in-progress: true" "tags: ['v*']" "branches: [main]" \
              "# 薄调用层：门禁规则本体在"; do
    if ! grep -qF -- "$key" "$ci"; then
      echo "skip $base: 调用层缺少关键结构「$key」⇒ 模板与真实调用层已漂移，不覆盖"
      rm -f "$rendered"
      return 0
    fi
  done
  cp "$rendered" "$ci"
  rm -f "$rendered"

  git -C "$dir" add .github/workflows/ci.yml
  git -C "$dir" commit -q -m "ci: 收敛到共享 reusable workflow oagentkit/.github/go-ci" \
    -m "规则统一在 oagentkit/.github 维护；本仓只保留按仓参数（go ${gover}）。嵌套模块交由 test-nested 自动测试。"
  if [ "$MODE" = pr ]; then
    git -C "$dir" push -q -u origin "$BRANCH"
    (cd "$dir" && gh pr create --fill >/dev/null)
    git -C "$dir" checkout -q main
  else
    git -C "$dir" push -q origin HEAD:main
  fi
  echo "done $base (go ${gover}, threshold ${thr:-默认30})"
}

if [ "${1:-}" = "--selftest" ]; then
  # 模板漂移自检：渲染 vs 矩阵内任一真实调用层。
  # 参考文件默认取矩阵根下**任一**已对齐的仓的调用层；也可跟一个参数指定别的文件。
  # ⚠️ 参考文件是**第 2 个**参数（$1 是 "--selftest" 本身）。
  shift
  HERE=$(cd "$(dirname "$0")" && pwd)
  ROOT="${MATRIX_ROOT:-$(cd "$HERE/../.." && pwd)}"
  REF_CI="${1:-}"
  if [ -z "$REF_CI" ] || [ ! -f "$REF_CI" ]; then
    REF_CI="$(ls "$ROOT"/*/.github/workflows/ci.yml 2>/dev/null | head -1)"
  fi
  if [ -z "$REF_CI" ] || [ ! -f "$REF_CI" ]; then
    echo "selftest: ❌ 找不到参考调用层（矩阵根 $ROOT 下没有 */.github/workflows/ci.yml）⇒ 判不了，不报绿" >&2
    exit 2
  fi
  selftest_render "$REF_CI"
  exit $?
fi

for d in "$@"; do sync_repo "$d"; done

