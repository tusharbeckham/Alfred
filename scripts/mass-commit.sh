#!/usr/bin/env bash
# Distribute all current work across multiple branches as 50+ small commits.
# Branches are chained (each starts from the previous tip) so files accumulate
# cleanly with no conflicts. Run:  bash scripts/mass-commit.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

CO="Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
SELF="scripts/mass-commit.sh"
COUNT=0

commit_file() {
  local f="$1"
  [ -e "$f" ] || return 0
  [ "$f" = "$SELF" ] && return 0
  local verb="update"
  git ls-files --error-unmatch -- "$f" >/dev/null 2>&1 || verb="add"
  git add -- "$f"
  git diff --cached --quiet -- "$f" && return 0   # nothing staged, skip
  git commit -q -m "$verb ${f}" -m "$CO"
  COUNT=$((COUNT+1))
  echo "  [$verb] $f  (#$COUNT)"
}

branch() {
  git checkout -q -B "$1"
  echo "== branch: $1 =="
}

# Commit every file matching a shell glob, one commit each.
commit_glob() {
  local g
  for g in "$@"; do
    for f in $g; do
      commit_file "$f"
    done
  done
}

echo ">> starting from $(git rev-parse --abbrev-ref HEAD)"

# 1. Shared backend brain + engine
branch feat/backends-engine
commit_glob scripts/backends.py scripts/workflow.py scripts/ultron.py \
            scripts/test_backends.py scripts/test_workflow.py \
            scripts/sync-claude-config.py

# 2. Generated Claude agent layer (one commit per agent)
branch feat/claude-agents
commit_glob ".claude/agents/*.md"

# 3. Generated Claude skills (one commit per skill)
branch feat/claude-skills
commit_glob ".claude/skills/*/SKILL.md" ".claude/skills/*/*.md"

# 4. Slash commands + settings
branch feat/claude-commands
commit_glob ".claude/commands/*.md" .claude/settings.json

# 5. MCP server + config
branch feat/mcp-server
commit_glob mcp/alfred-server.js .mcp.json

# 6. Lifecycle hooks
branch feat/hooks
commit_glob "hooks/*.ps1"

# 7. Docs + governance
branch feat/docs
commit_glob docs/claude-integration.md docs/orchestration/workflow-engine.md \
            README.md AGENTS.md CLAUDE.md

# 8. Misc (gitignore, lsp, deploy assets)
branch feat/misc
commit_glob .gitignore .kiro/settings/lsp.json "deploy/cloudflare/*.png"

# Sweep: anything still uncommitted lands on a final branch, split further
branch feat/remainder
while read -r f; do commit_file "$f"; done < <(git ls-files --others --exclude-standard --modified | grep -v "^$SELF$")

echo ""
echo ">> TOTAL COMMITS: $COUNT across 9 branches"
git branch --list 'feat/*'
