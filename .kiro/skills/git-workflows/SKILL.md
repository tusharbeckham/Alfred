---
name: git-workflows
description: Git branching, commits, PRs, and worktree-based parallel/multi-repo development. Use for any version-control operation or when orchestrating work across repos.
---

# Git Workflows

## Safety first
- NEVER push to `main`/`master` unless the Owner explicitly asks.
- NEVER force-push, `reset --hard`, `clean -f`, or delete branches without approval.
- Stage specific files; never blind `git add .`. Flag possible secrets before commit.

## Standard flow
1. `git switch -c feature/<short-name>` off the up-to-date base.
2. Small, focused commits with imperative messages: `feat:`, `fix:`, `chore:`, `docs:`.
3. Push the branch with `-u`. Open a PR (`gh pr create`) with summary + tests + risks.
4. Keep PRs < ~400 lines where possible.

## Worktrees (parallel & multi-repo)
Worktrees let multiple branches be checked out at once in separate folders — ideal for
running several coder agents in parallel without clobbering each other.

```powershell
# One worktree per parallel task / per repo
git worktree add ../wt-featureA feature/A
git worktree add ../wt-featureB feature/B
# ... agents work in their own dir ...
git worktree remove ../wt-featureA    # when merged/abandoned
git worktree list                      # audit
```

Rules:
- One worktree per subagent stage. Name it after the task/branch.
- Clean up worktrees when the stage completes.
- For multi-repo: create a worktree per repo, run coders in parallel, fan in to review.

## Recovery (ask before destructive)
- Prefer `git revert` (new commit) over history rewrite.
- `git stash` to park WIP. `git reflog` to find lost commits before any reset.

## PR hygiene
- Title < 70 chars. Description: what/why, how tested, anything blocked.
- Link related issues. Request review from `alfred-reviewer` before merge.
