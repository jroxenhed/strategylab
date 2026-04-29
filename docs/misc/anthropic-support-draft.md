# Anthropic Support — Remote Routine Can't Push to GitHub

## Subject
Claude Code Routine: git push fails with 403 from sandbox git proxy

## Message

Hi,

I set up a Claude Code scheduled routine (remote agent) that implements features from my TODO backlog overnight. The routine successfully clones my repo, reads project files, implements changes, verifies builds, and commits — but fails on `git push origin main` with a 403 from the local git proxy (`127.0.0.1:36457`).

**Setup:**
- Routine ID: `trig_01VAJyHdiq4TKiCiBbp1wCu3`
- Repo: `https://github.com/jroxenhed/strategylab` (public)
- GitHub Integration connected at claude.ai/customize/connectors
- Claude GitHub App authorized under GitHub Settings → Authorized GitHub Apps

**What works:**
- Git clone (the routine clones the repo fine)
- All file operations (Read, Write, Edit, Bash)
- npm install, npm run build
- git add, git commit

**What fails:**
- `git push origin main` → 403 from `127.0.0.1:36457` (the sandbox git proxy)
- Tried multiple times across two separate runs
- Same error whether repo is public or private
- The agent's own diagnosis: "credential/proxy issue, not transient"

**Runs showing the issue:**
- First run completed at 0:43 CEST on 2026-04-30 — 6 commits built, all stranded
- Second run at 1:33 CEST — same 403

**Question:**
Does the remote sandbox's git proxy support push? If so, what's needed to grant write access — is there a GitHub App installation step I'm missing, or does the OAuth integration need additional scopes?

If push isn't supported yet, is there a timeline? Alternatively, would `gh pr create` work through a different auth path?

Thanks,
John
