#!/usr/bin/env bash
# Build a portable "field kit" tarball that carries the Claude Code context
# this repo deliberately gitignores (CLAUDE.md, .claude/, auto-memory) so a
# customer-machine clone can be made Claude-ready with one script.
#
# Run from anywhere:  bash scripts/field_kit.sh   (or: make field-kit)
# Output:             dist/claude-field-kit.tar.gz
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
KIT_NAME=claude-field-kit
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
KIT="$STAGE/$KIT_NAME"

mkdir -p "$KIT/project/.claude" "$KIT/global" "$KIT/memory"

# --- project-level context (gitignored in this repo) -----------------------
cp "$REPO_ROOT/CLAUDE.md" "$KIT/project/CLAUDE.md"
for d in rules commands; do
  if [ -d "$REPO_ROOT/.claude/$d" ]; then
    cp -R "$REPO_ROOT/.claude/$d" "$KIT/project/.claude/$d"
  fi
done
# deliberately NOT shipped: .claude/settings.local.json, .claude/worktrees

# --- user-global instructions ----------------------------------------------
if [ -f "$HOME/.claude/CLAUDE.md" ]; then
  cp "$HOME/.claude/CLAUDE.md" "$KIT/global/CLAUDE.md"
fi

# --- auto-memory for this checkout ------------------------------------------
# Claude Code names the projects dir by replacing every non-alphanumeric
# character of the checkout path with '-'.
PROJECT_KEY=$(printf '%s' "$REPO_ROOT" | LC_ALL=C sed 's/[^a-zA-Z0-9]/-/g')
MEM_SRC="$HOME/.claude/projects/$PROJECT_KEY/memory"

# Memory files NOT shipped: home-environment workflows (deploy/push/test-instance
# targets) that are wrong or misleading on a customer machine, plus stale
# merged-branch history. MEMORY.md is skipped here and replaced by the curated
# index in scripts/field-kit-overrides/ (which also sanitizes files whose
# pattern is useful but whose specifics are home-only, e.g. live API access).
MEMORY_SKIP='
MEMORY.md
feedback_auto_deploy_after_build.md
feedback_test_instance.md
project_secure_push_gate.md
project_refactor_devibe_branch.md
project_slick_fx_worktree.md
reference_tam_repo_pr_flow.md
reference_webapp_public_no_auth.md
'

if [ -d "$MEM_SRC" ]; then
  skipped=0
  for f in "$MEM_SRC"/*.md; do
    base=$(basename "$f")
    if printf '%s\n' "$MEMORY_SKIP" | grep -qxF "$base"; then
      skipped=$((skipped + 1))
      continue
    fi
    cp "$f" "$KIT/memory/"
  done
  echo "[field-kit] Memory: skipped $skipped home-only file(s) per sanitize list"
else
  echo "[field-kit] WARNING: no memory dir at $MEM_SRC - kit will ship without memory" >&2
fi

# Sanitized override copies replace/supplement the originals
cp "$REPO_ROOT/scripts/field-kit-overrides/"*.md "$KIT/memory/"

cp "$REPO_ROOT/scripts/field_kit_install.sh" "$KIT/install.sh"
chmod +x "$KIT/install.sh"

mkdir -p "$REPO_ROOT/dist"
OUT="$REPO_ROOT/dist/$KIT_NAME.tar.gz"
tar -czf "$OUT" -C "$STAGE" "$KIT_NAME"

echo "[field-kit] Built: $OUT"
echo "[field-kit] Contents:"
tar -tzf "$OUT" | sed 's/^/  /'
echo
echo "[field-kit] Memory is sanitized via MEMORY_SKIP + scripts/field-kit-overrides/."
echo "[field-kit] NEW memory files ship by default - review any added since the"
echo "[field-kit] sanitize list was last curated (2026-06-11)."
echo
echo "[field-kit] On the customer machine (AlmaLinux 8/9/10 or any Linux):"
echo "  1. git clone <repo> /data/<dir>/dss-admin-toolkit && cd into it"
echo "  2. tar -xzf $KIT_NAME.tar.gz"
echo "  3. bash $KIT_NAME/install.sh /path/to/dss-admin-toolkit"
