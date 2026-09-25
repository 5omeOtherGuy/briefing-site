#!/usr/bin/env bash
# install-git-hooks.sh: install the pre-push secret-scan hook into this
# repository's hooks directory (the common dir, so every worktree gets it).
# Idempotent; only the pre-push hook shipped in tools/hooks/ is touched.
set -eu
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HOOKS=$(git -C "$REPO_ROOT" rev-parse --git-path hooks)
case "$HOOKS" in /*) ;; *) HOOKS="$REPO_ROOT/$HOOKS";; esac
mkdir -p "$HOOKS"
install -m 0755 "$REPO_ROOT/tools/hooks/pre-push" "$HOOKS/pre-push"
echo "installed $HOOKS/pre-push"
