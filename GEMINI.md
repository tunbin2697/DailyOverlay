# Antigravity Workspace Rules — DailyOverlay

## Git & GitHub Safety (STRICT)

1. **Never auto-commit:** Do NOT run `git commit` (including `--amend`), `git add`, `git reset`, `git rebase`, `git checkout --`, or any command that modifies git history or staging state unless the user explicitly asks you to do so in that turn.
2. **Never interact with GitHub automatically:** Do NOT run `git push`, `git pull`, `gh` CLI commands, create/edit/merge/delete Pull Requests, create/delete branches on remotes, or modify GitHub issues/releases unless the user explicitly instructs and confirms the exact action.
3. **Never delete or rewrite commits/PRs:** Never run destructive git operations (`git push --force`, `git reset --hard`, `git branch -D`, PR deletion/closing) without explicit user approval.
4. **Public repository hygiene:** This repository is public on GitHub. Never write API keys, secrets, `.env` values, or personal local file paths (`C:\Users\...`) into tracked files. Always verify `.gitignore` protects `.env` and generated user data before suggesting a commit.
