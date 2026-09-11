English | [简体中文](README.md)

# wb-conversation-cleanup

> You clicked "Delete conversation" in WorkBuddy. It didn't actually delete anything.
> The file sits on your disk until you do something about it.

A tool to **thoroughly wipe WorkBuddy's local conversation records** — runnable standalone, or installed as an Agent Skill that triggers conversationally.

---

## 1. Where this came from

A concrete scenario: you poke around your home directory one day and find `~/.workbuddy/projects/` cluttered with a dozen folders. Open one and it's `.jsonl` — line-delimited JSON, plaintext, recording every turn in full.

Including:

- Every message you typed
- The model's reasoning
- **The complete input and output of every tool call** (file paths, command output, API responses)

Then you notice something worse: **conversations you already deleted in the UI still have their full transcripts on disk.**

Observed in practice: a dozen-plus folders under `projects/`, all plaintext, several of them long since "deleted" in the UI — yet every transcript intact.

---

## 2. Why build it

### Because "delete" is misleading here

Deleting a session in the WorkBuddy UI does exactly this:

```sql
UPDATE sessions SET deleted_at = <timestamp> WHERE id = <session-uuid>
```

**That's all.** It disappears from the list; not a single file moves.

Which leaves three real problems:

| Problem | Consequence |
|---|---|
| **Privacy residue** | You think it's gone. Anyone with folder access can read it — including full tool-call context, far denser than the chat text itself |
| **Disk space never comes back** | It accumulates. Cache directories like `blobs` and `traces` grow even faster and routinely reach hundreds of MB |
| **No batch operation** | Finding files means eyeballing UUIDs, which have no readable mapping to conversation titles |

### Because existing options all suck

- **Manual deletion** — look up UUIDs in SQLite, then match filenames. Tolerable for a dozen, not for dozens
- **Reinstalling the app** — wildly disproportionate to clearing chat history
- **Third-party cleaners** — you can't tell what they actually delete, which is worse

So we need something where you **can see what's being deleted, can undo it, and can do it in bulk**.

---

## 3. How it works

### Where the data lives

| Content | Path |
|---|---|
| Transcript | `~/.workbuddy/projects/<project-id>/<session-uuid>.jsonl` |
| Rollback snapshot | `<uuid>.file-rollback.ndjson` in the same folder |
| Session metadata | `<uuid>.meta.json` in the same folder |
| Soft-delete flag | `~/.workbuddy/workbuddy.db` → `sessions.deleted_at` |
| Attachment cache | `~/.workbuddy/blobs`, `file-history`, `traces`, `artifact-index` |

### What the tool does

1. Reads `workbuddy.db` to get which sessions are flagged deleted, plus their titles — turning UUIDs back into something a human can read
2. Scans `projects/`, grouping `<uuid>.jsonl` / `.file-rollback.ndjson` / `.meta.json` as one unit
3. **Shows you a list first**, labeled with size and title
4. Deletes only after confirmation, then reclaims now-empty project folders

---

## 4. Install

### As a Skill (recommended for WorkBuddy / Claude Code users)

Same SKILL.md for both tools — only the install directory differs:

```bash
# WorkBuddy
git clone https://github.com/DF-Guan/wb-conversation-cleanup.git \
    ~/.workbuddy/skills/wb-conversation-cleanup

# Claude Code
git clone https://github.com/DF-Guan/wb-conversation-cleanup.git \
    ~/.claude/skills/wb-conversation-cleanup
```

Then just say, in conversation:

> clean up conversations / delete chat history / free disk space / clear cache

The agent loads it and follows the workflow.

> **For Claude Code users:** invoking a skill does *not* change the working directory, so always resolve the script path with `${CLAUDE_SKILL_DIR}`:
> ```bash
> python "${CLAUDE_SKILL_DIR}/scripts/cleanup.py" --list
> ```
> SKILL.md states this as a mandatory first step.

### As a standalone script (no AI tooling needed)

```bash
git clone https://github.com/DF-Guan/wb-conversation-cleanup.git
cd wb-conversation-cleanup
python scripts/cleanup.py          # launches the menu
```

Windows users can just double-click `Run-Cleanup.bat`.

---

## 5. Usage

### Skill mode (conversation-driven, least friction)

```text
You:    clean up my conversations
Agent:  here's the list first -> okay to remove X MB?
You:    yes
Agent:  please quit WorkBuddy -> runs -> reports how much space was freed
```

### CLI mode (for direct control)

```bash
# List first, delete nothing (never skip this step)
python scripts/cleanup.py --list

# What's actually eating your disk (largest first)
python scripts/cleanup.py --list --top 10

# Preview what a cache-only cleanup would look like
python scripts/cleanup.py --list --cache-only

# [Most common] Clear cache only, keep every conversation
python scripts/cleanup.py --cache-only --backup --yes

# Only conversations already deleted in the UI
python scripts/cleanup.py --soft-deleted --backup --yes

# Only conversations older than 30 days (supports 30d / 6m / 1y)
python scripts/cleanup.py --older-than 30d --backup --yes

# Delete everything
python scripts/cleanup.py --all --backup --yes

# Everything + cache directories
python scripts/cleanup.py --all --include-cache --backup --yes

# Changed your mind — restore from backup
python scripts/cleanup.py --restore ~/Desktop/WB-Cleanup-Backup/files-0911-092353
```

### Options

| Option | Effect |
|---|---|
| `--list` | Preview only, never deletes. **Combines with other modes** (`--list --cache-only`) |
| `--cache-only` | **Clears cache directories only, keeps every conversation** |
| `--soft-deleted` | Only sessions where `deleted_at` is set |
| `--older-than AGE` | Only sessions older than the given age: `30d` / `6m` / `1y` |
| `--all` | Every session |
| `--top N` | Keep only the N largest items, descending by size |
| `--include-cache` | Also clear blobs / file-history / traces / artifact-index |
| `--backup` | Back up before deleting to `~/Desktop/WB-Cleanup-Backup/<timestamp>/` (includes `_manifest.txt`) |
| `--restore DIR` | Restore from a backup directory, back to the **original paths** |
| `--purge-db` | Additionally clear the session list in the database (database backed up first) |
| `--yes` | Skip the `DELETE` confirmation prompt |
| `--backup-dir PATH` | Custom backup root |

---

## 6. Safety design

Deletion is irreversible, so this tool is deliberately conservative in several places:

1. **Preview is mandatory** — in Skill mode, SKILL.md hard-requires showing the user the list before any deletion
2. **Process guard** — refuses to delete while WorkBuddy is running (preview excepted), so nothing is half-deleted
3. **Double confirmation** — type `DELETE`, or use `--yes` only after the user has already agreed in conversation
4. **Backups are the default recommendation** — regrets need a door
5. **Backups are restorable** — each backup carries `_manifest.txt` with the original absolute paths; `--restore` puts files back where they came from
6. **Strictly scoped** — touches only `projects/`, the four cache directories and `workbuddy.db`. Never `skills/`, `connectors/` or `credentials/`
7. **`--purge-db` backs up the database first** — including `.db`, `-wal` and `-shm`
8. **Audit trail** — every deletion appends a line to `~/.workbuddy/cleanup-audit.log`

### Order matters

> **Delete in the UI first (to set the soft-delete flag), then run the script.**

Reversing it leaves the database and disk out of sync. `--soft-deleted` depends on that flag.

---

## 7. FAQ

**Q: The UI list still shows them after deleting?**
Expected. Deleting files doesn't remove the list; database records live independently. Use `--purge-db` to clear the list too.

**Q: `Remove-Item` throws `trash-failed`?**
It usually deleted the file anyway (Windows Recycle Bin behavior). **Re-list the directory to confirm** — don't trust the error alone.

**Q: Why do some entries have no title?**
A few sessions (e.g. background automations) have no title in the database, so the tool falls back to their project folder name.

**Q: Can I delete the conversation I'm in right now?**
No — the file is in use. It becomes deleteable once WorkBuddy exits; no special handling needed.

**Q: I only want disk space back, not my conversations?**
Use `--cache-only`. `blobs` and `traces` are usually much larger than the transcripts themselves.

**Q: `--all --include-cache` used to delete conversations too — scary?**
That was a 1.0 design flaw, fixed in 1.1: cache cleanup is now its own mode, no longer bundled with deleting conversations.

**Q: How often should I clean?**
`blobs` and `traces` grow fastest. Roughly once a month with `--cache-only`.

**Q: As a Claude Code skill it says "script not found"?**
Invoking a skill doesn't change the working directory, so relative paths break. Use `${CLAUDE_SKILL_DIR}/scripts/cleanup.py`.

---

## 8. ⚠️ Before you run it

**This tool permanently deletes files.**

- First run? Start with `--list` to review the list
- Unsure? Add `--backup`
- Data is irreplaceable — know what you're deleting before you delete it

When used as a Skill, note that the agent gains the ability to delete your local files. SKILL.md enforces a "preview before delete" rule; if you modify that constraint, you assume the risk.

---

## 9. Compatibility

| Platform | Status |
|---|---|
| Windows | ✅ Tested (Recycle Bin behavior, PowerShell encoding) |
| macOS / Linux | ⚠️ Supported in code (`pgrep` detection, `expanduser("~")` paths) — **not yet tested on real hardware** |
| Claude Code | ✅ Compatible (all frontmatter fields supported); requires `${CLAUDE_SKILL_DIR}` to locate the script |
| WorkBuddy | ✅ Native support |

Requires Python 3.9+, **no third-party dependencies**. Three platforms × three Python versions are tested automatically via GitHub Actions.

### Run the checks yourself

```bash
python tests/test_cleanup.py
```

Eight cases, all running against temp directories and fake data — nothing touches your real files.

---

## License

MIT
