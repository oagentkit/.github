#!/usr/bin/env bash
# 给各模块仓安装「即时入板」workflow + OAGENTKIT_PROJECT_TOKEN secret。
# 用 GitHub API 写文件，不依赖本地是否 clone。
#
# 用法：
#   bash scripts/sync-project-hooks.sh --dry                  # 只列出目标仓
#   bash scripts/sync-project-hooks.sh --only matrix-tools,llm,guardx
#   bash scripts/sync-project-hooks.sh                        # 铺满全部模块仓
#
# 幂等：已存在的 workflow 按内容比对后跳过；secret 已存在则不重复写。
set -euo pipefail

ORG=${ORG:-oagentkit}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/workflow-templates/issue-to-project.yml"
TOKEN=$(gh auth token)
SECRET_NAME=OAGENTKIT_PROJECT_TOKEN
MSG="ci: issue/PR 开闭即时入板 org 大盘（复用 oagentkit/.github sync-projects）"
PATH_IN_REPO=".github/workflows/issue-to-project.yml"

DRY=0
ONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry) DRY=1; shift ;;
    --only) ONLY="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi

# 模块清单真相源 = matrix-tools/modules.json（modules + infraRepos）
MANIFEST_JSON=$(gh api "repos/$ORG/matrix-tools/contents/modules.json" --jq '.content' | tr -d '\n' | base64 --decode)
MODULES=$(python3 - "$MANIFEST_JSON" <<'PY'
import json, sys
d = json.loads(sys.argv[1])
names = [m["repo"] for m in d.get("modules", []) if isinstance(m, dict) and m.get("repo")]
names += [r["repo"] for r in d.get("infraRepos", []) if isinstance(r, dict) and r.get("repo")]
print("\n".join(names))
PY
)

if [[ -n "$ONLY" ]]; then
  TARGETS=$(echo "$ONLY" | tr ',' '\n' | sed '/^$/d')
else
  TARGETS=$(echo "$MODULES")
fi

CONTENT=$(base64 < "$SRC" | tr -d '\n')
CONTENT_SHA=$(printf '%s' "$CONTENT" | shasum -a 256 | cut -d' ' -f1)

for mod in $TARGETS; do
  echo "=== $mod ==="
  if [[ "$DRY" == "1" ]]; then
    echo "  dry: $ORG/$mod"
    continue
  fi

  # secret
  if ! gh secret list --repo "$ORG/$mod" 2>/dev/null | grep -q "$SECRET_NAME"; then
    echo "$TOKEN" | gh secret set "$SECRET_NAME" --repo "$ORG/$mod"
    echo "  + secret $SECRET_NAME"
  else
    echo "  = secret already set"
  fi

  # workflow 文件（contents API，幂等；内容一致则不产生新 commit）
  SHA=$(gh api "repos/$ORG/$mod/contents/$PATH_IN_REPO" --jq .sha 2>/dev/null || true)
  if [[ -n "$SHA" && "$SHA" != "null" ]]; then
    EXISTING=$(gh api "repos/$ORG/$mod/contents/$PATH_IN_REPO" --jq '.content' | tr -d '\n' | base64 --decode | shasum -a 256 | cut -d' ' -f1)
    if [[ "$EXISTING" == "$CONTENT_SHA" ]]; then
      echo "  = workflow 已是最新，跳过"
      continue
    fi
  fi
  PAYLOAD=$(jq -n \
    --arg message "$MSG" \
    --arg content "$CONTENT" \
    --arg sha "${SHA:-}" \
    '{message:$message, content:$content, branch:"main"}
     + (if $sha != "" and $sha != "null" then {sha:$sha} else {} end)')
  if gh api -X PUT "repos/$ORG/$mod/contents/$PATH_IN_REPO" --input - <<<"$PAYLOAD" >/dev/null; then
    echo "  ✓ workflow written"
  else
    echo "  ! workflow write failed" >&2
    exit 1
  fi
done

echo "DONE"
