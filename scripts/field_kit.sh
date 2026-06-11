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
if [ -d "$MEM_SRC" ]; then
  cp "$MEM_SRC"/*.md "$KIT/memory/"
else
  echo "[field-kit] WARNING: no memory dir at $MEM_SRC - kit will ship without memory" >&2
fi

cp "$REPO_ROOT/scripts/field_kit_install.sh" "$KIT/install.sh"
chmod +x "$KIT/install.sh"

mkdir -p "$REPO_ROOT/dist"
OUT="$REPO_ROOT/dist/$KIT_NAME.tar.gz"
tar -czf "$OUT" -C "$STAGE" "$KIT_NAME"

echo "[field-kit] Built: $OUT"
echo "[field-kit] Contents:"
tar -tzf "$OUT" | sed 's/^/  /'
echo
echo "[field-kit] REVIEW BEFORE TAKING TO A CUSTOMER: the memory/ files contain"
echo "[field-kit] internal instance names and URLs (tam-global, gozu fork, ...)."
echo
echo "[field-kit] On the customer machine (AlmaLinux 8/9/10 or any Linux):"
echo "  1. git clone <repo> /data/<dir>/dss-admin-toolkit && cd into it"
echo "  2. tar -xzf $KIT_NAME.tar.gz"
echo "  3. bash $KIT_NAME/install.sh /path/to/dss-admin-toolkit"
