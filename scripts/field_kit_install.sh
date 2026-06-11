#!/usr/bin/env bash
# Field-kit installer - run ON THE CUSTOMER MACHINE (any Linux; tested target
# is AlmaLinux 8/9/10; needs only bash, sed, cp and - for the optional Claude
# Code install - curl with outbound HTTPS).
#
#   usage: bash install.sh [target-dir]
#          (default target: ./dss-admin-toolkit)
#
# Self-sufficient: the kit carries the repo as repo.bundle. If the target is
# missing or an empty dir, it is git-cloned from the bundle; if it is already
# a dss-admin-toolkit checkout, it is reused as-is. Then the kit restores
# everything the repo gitignores:
#   project/CLAUDE.md + .claude/   -> into the checkout
#   global/CLAUDE.md               -> ~/.claude/CLAUDE.md (never clobbers)
#   memory/*.md                    -> ~/.claude/projects/<derived-key>/memory/
# and installs Claude Code if it is not already on PATH.
set -euo pipefail

KIT_DIR=$(cd "$(dirname "$0")" && pwd -P)

TARGET=${1:-dss-admin-toolkit}
if [ -f "$TARGET/plugin.json" ] && [ -f "$TARGET/webapps/admin-toolkit/backend.py" ]; then
  TARGET=$(cd "$TARGET" && pwd -P)
  echo "==> Using existing checkout: $TARGET"
elif [ ! -e "$TARGET" ] || { [ -d "$TARGET" ] && [ -z "$(ls -A "$TARGET" 2>/dev/null)" ]; }; then
  if ! command -v git >/dev/null 2>&1; then
    echo "error: git is required to unpack the embedded repo (e.g. dnf install git)" >&2
    exit 1
  fi
  if [ ! -f "$KIT_DIR/repo.bundle" ]; then
    echo "error: kit is missing repo.bundle - rebuild it with 'make field-kit'" >&2
    exit 1
  fi
  echo "==> Cloning embedded repo -> $TARGET"
  git clone "$KIT_DIR/repo.bundle" "$TARGET"
  TARGET=$(cd "$TARGET" && pwd -P)
else
  echo "error: $TARGET exists but is neither empty nor a dss-admin-toolkit checkout" >&2
  echo "usage: bash install.sh [target-dir]" >&2
  exit 1
fi

echo "==> Project context -> $TARGET"
cp "$KIT_DIR/project/CLAUDE.md" "$TARGET/CLAUDE.md"
mkdir -p "$TARGET/.claude"
cp -R "$KIT_DIR/project/.claude/." "$TARGET/.claude/"

if [ -f "$KIT_DIR/global/CLAUDE.md" ]; then
  mkdir -p "$HOME/.claude"
  if [ -f "$HOME/.claude/CLAUDE.md" ]; then
    echo "==> ~/.claude/CLAUDE.md already exists - left untouched"
    echo "    (kit copy kept at $KIT_DIR/global/CLAUDE.md if you want to merge)"
  else
    cp "$KIT_DIR/global/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
    echo "==> Global instructions -> ~/.claude/CLAUDE.md"
  fi
fi

# Claude Code derives the per-project dir from the ABSOLUTE checkout path,
# replacing every non-alphanumeric character with '-'. Compute it for THIS
# machine's path so memory is found when claude is started inside $TARGET.
PROJECT_KEY=$(printf '%s' "$TARGET" | LC_ALL=C sed 's/[^a-zA-Z0-9]/-/g')
MEM_DIR="$HOME/.claude/projects/$PROJECT_KEY/memory"
if ls "$KIT_DIR/memory/"*.md >/dev/null 2>&1; then
  mkdir -p "$MEM_DIR"
  cp "$KIT_DIR/memory/"*.md "$MEM_DIR/"
  echo "==> Memory ($(ls "$KIT_DIR/memory/"*.md | wc -l | tr -d ' ') files) -> $MEM_DIR"
else
  echo "==> Kit contains no memory files - skipping memory install"
fi

if command -v claude >/dev/null 2>&1; then
  echo "==> Claude Code already installed: $(command -v claude)"
else
  echo "==> Claude Code not found - installing (needs outbound HTTPS) ..."
  curl -fsSL https://claude.ai/install.sh | bash
  echo "==> If 'claude' is still not found, add ~/.local/bin to PATH:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

cat <<EOF

Done. Next steps:
  cd "$TARGET"
  # auth (pick one):
  #   export CLAUDE_CODE_OAUTH_TOKEN=<token from 'claude setup-token' run at home>
  #   export ANTHROPIC_API_KEY=<api key>
  #   or just run 'claude' and follow the interactive login
  claude
EOF
