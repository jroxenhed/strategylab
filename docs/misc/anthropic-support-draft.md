# Anthropic Support — Remote Routine Can't Push to GitHub

## Subject
Claude Code Routine: git push still fails with 403 after enabling "Allow unrestricted git push"

## Message

Hi,

Following up on my earlier report. The support bot suggested enabling "Allow unrestricted git push" in the routine's Permissions tab and using `claude/` prefixed branches. I've done both — the permission toggle is ON and the branch name starts with `claude/` — but pushes still fail with the same 403.

**Setup:**
- Routine ID: `trig_01VAJyHdiq4TKiCiBbp1wCu3`
- Repo: `https://github.com/jroxenhed/strategylab` (public)
- "Allow unrestricted git push" toggle: ON (enabled via routine edit UI)
- GitHub Integration connected at claude.ai/customize/connectors
- Claude GitHub App authorized under GitHub Settings → Authorized GitHub Apps

**What I tried based on support bot advice:**
1. Enabled "Allow unrestricted git push" in routine Permissions tab
2. Updated routine prompt to use `claude/overnight-YYYY-MM-DD` branches
3. Triggered a fresh run (new session, not reusing old one)
4. The new session created branch `claude/dreamy-albattani-cDc4K`

**Still failing:**
- `git push -u origin claude/dreamy-albattani-cDc4K` → 403
- `git push` with retries (4s and 8s backoff) → 403
- GitHub MCP file push fallback → API stream idle timeout
- Error: `remote: Permission to jroxenhed/strategylab.git denied to jroxenhed.`
- The `allow_unrestricted_git_push: true` flag IS present in the routine's API response (confirmed via `RemoteTrigger get`)

**All runs showing the issue (5 total):**
- Run 1 (2026-04-30 00:43 CEST) — 3 features built, push 403
- Run 2 (2026-04-30 01:33 CEST) — push 403
- Run 3 (2026-04-30 02:01 CEST, scheduled) — failed with stream idle timeout
- Run 4 (2026-04-30 20:50 CEST) — interacted with old session, push 403 (expected — old token)
- Run 5 (2026-04-30 21:08 CEST) — fresh run WITH permission toggle ON, still 403, MCP fallback timed out

**Key detail:** The permission toggle does not appear to affect the git proxy's token scope. The proxy at `127.0.0.1:<port>` still returns 403 on `git-receive-pack` regardless of the toggle setting. Clone/fetch work fine.

**Question:**
Is there an additional step needed beyond the toggle? Does the GitHub App installation need specific repository permissions (e.g. "Contents: Read and write")? Or is there a propagation delay for the toggle to take effect?

The routine works perfectly end-to-end except for pushing — 5 runs, multiple features implemented and build-verified, all stranded in sandboxes.

Thanks,
John
