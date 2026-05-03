# Overnight Builder Prompt — Patch Notes

Apply these changes to the overnight builder prompt.

---

## 1. Change task limit

Find:
```
- Max 3 tasks per run (start conservative).
```

Replace with:
```
- Max 5 tasks per run.
```

---

## 2. Fix Python syntax check

Find:
```
For Python changes: python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"
```

Replace with:
```
For Python changes: python3 -c "import ast; ast.parse(open('file.py').read()); print('OK')"
(ast.parse catches verbatimModuleSyntax errors that py_compile misses)
```

---

## 3. Add Known Patterns section (after Guard Rails)

```markdown
## Known Patterns (do not regress)

These patterns exist for non-obvious safety reasons. If your changes touch these files, verify the patterns survive.

- **Atomic bots.json writes (F14):** `bot_manager.py save()` uses `tempfile.NamedTemporaryFile` + `os.replace()`. Never write to `DATA_PATH` directly — a crash mid-write would lose all bot config/state.
- **Atomic journal writes (F16):** `journal.py _log_trade()` holds `_journal_lock` around the read-modify-write and uses atomic tmp+replace for the write. Never bypass the lock or use `write_text()` directly — concurrent bot ticks can lose trade records.
- **Journal errors must be logged (F15):** Every `_log_trade()` call site wraps in `except Exception as e: self._log("ERROR", ...)`. Never change these to `except Exception: pass` — a swallowed journal error means a trade executes at the broker with no record.
- **Opposite-direction guard skips on failure (D25):** `bot_runner.py` section 6 returns (skips entry) when the position check raises. Never change to `pass` — proceeding on broker failure risks double-entry with real money.
- **bot_runner.py test+split gate (F20→F21):** Avoid adding new features to `bot_runner.py` until F20 (test harness) and F21 (file split) are complete. New logic in the untested 1000-line monolith increases the risk of undetected bugs.
```

---

## 4. Add review quality note (in Self-Review section, after the 5 passes)

```markdown
### Self-Review Limitations

Your single-pass self-review catches surface issues but consistently misses P1 bugs that multi-agent review finds. In the 2026-05-03 session, your build 6 shipped with 0 self-review findings, but 3 independent reviewers found 2 P1s (hardcoded .tmp race, journal torn-read race). Accept this limitation — ship clean code, and the human will run multi-agent review before merging.
```
