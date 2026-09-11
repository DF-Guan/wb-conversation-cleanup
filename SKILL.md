---
name: wb-conversation-cleanup
description: "彻底清理 WorkBuddy 本地对话记录与缓存。界面里的删除只是软删除（sessions.deleted_at 打标记），正文 .jsonl 仍以明文留在磁盘；本技能用于真正从硬盘删除对话、回收空间、清理隐私残留。支持只清缓存、按时间清理、按体积筛选、备份与还原。"
when_to_use: "清理对话, 删除聊天记录, 彻底删除, 释放空间, 隐私清理, 对话还在硬盘上, 清理缓存, conversation cleanup, purge chat history"
allowed-tools: Bash(python:*)
metadata:
  version: "1.1.0"
---

# WorkBuddy 对话彻底清理

界面上的「删除」**不会**删掉本地文件。真正的数据在这里：

| 内容 | 路径 |
|---|---|
| 对话正文（jsonl） | `~/.workbuddy/projects/<项目ID>/<会话UUID>.jsonl` |
| 回滚快照 | 同目录 `<UUID>.file-rollback.ndjson` |
| 会话元信息 | 同目录 `<UUID>.meta.json` |
| 软删除标记 | `~/.workbuddy/workbuddy.db` → `sessions.deleted_at` |
| 附件缓存 | `~/.workbuddy/blobs`、`file-history`、`traces`、`artifact-index` |

正文是**明文 JSON**，含工具调用的完整入参回参。

## ⚠️ 第一步：确定脚本的绝对路径（不要跳过）

**调用技能不会切换工作目录**，`scripts/cleanup.py` 这种相对路径会直接找不到文件。必须先拼出绝对路径：

- **Claude Code** —— 用内置变量，它指向本 SKILL.md 所在目录：

  ```bash
  python "${CLAUDE_SKILL_DIR}/scripts/cleanup.py" --list
  ```

- **WorkBuddy** —— 用你加载本文件时读到的那个目录路径：

  ```bash
  python "<SKILL.md 所在目录>/scripts/cleanup.py" --list
  ```

后续所有命令都基于这个绝对路径，**不要先 `cd` 到别处再指望相对路径生效**。

## 执行流程（必须按序）

1. **先预览，永远不要跳过**
   ```bash
   python "$DIR/scripts/cleanup.py" --list
   ```
   脚本会列出对话与缓存各自占用多少。**把清单展示给用户，让用户确认范围。**

   想帮用户判断「到底谁占地方」，加 `--top N`（按体积倒序取前 N）：
   ```bash
   python "$DIR/scripts/cleanup.py" --list --top 10
   ```

2. **提醒用户完全退出 WorkBuddy**（含托盘图标）。脚本检测到进程在跑会自行中止。

3. **得到用户明确同意后才执行删除**，推荐先备份：

   ```bash
   # 只清缓存，保留所有对话（最常用，也是增长最快的部分）
   python "$DIR/scripts/cleanup.py" --cache-only --backup --yes

   # 只清理界面里已删除的那些
   python "$DIR/scripts/cleanup.py" --soft-deleted --backup --yes

   # 清理早于指定时间的对话（30天/6个月/1年）
   python "$DIR/scripts/cleanup.py" --older-than 30d --backup --yes

   # 清理全部对话
   python "$DIR/scripts/cleanup.py" --all --backup --yes

   # 全部对话 + 缓存
   python "$DIR/scripts/cleanup.py" --all --include-cache --backup --yes
   ```

4. **告知结果**：释放了多少空间、备份在哪里、审计日志在 `~/.workbuddy/cleanup-audit.log`。

## 参数

| 参数 | 作用 |
|---|---|
| `--list` | 只预览，绝不删除。**可与其他模式组合**（如 `--list --cache-only`） |
| `--cache-only` | 只清缓存目录，**保留所有对话** |
| `--soft-deleted` | 只处理 `deleted_at` 非空的会话 |
| `--older-than AGE` | 只处理早于该时间的会话，支持 `30d` / `6m` / `1y` |
| `--all` | 处理所有会话 |
| `--top N` | 按体积倒序只取前 N 项 |
| `--include-cache` | 附带清理 blobs / file-history / traces / artifact-index |
| `--backup` | 删除前备份到 `~/Desktop/WB-Cleanup-Backup/<时间戳>/`（含 `_manifest.txt`） |
| `--restore DIR` | 从备份目录还原（依赖其中的 `_manifest.txt`） |
| `--purge-db` | 额外清空数据库里的会话列表（会先备份数据库） |
| `--yes` | 跳过 DELETE 确认。**仅当用户已在对话中确认后使用** |
| `--backup-dir PATH` | 自定义备份根目录 |

## 硬规则

- **绝不跳过预览直接删。** 没有列出清单给用户看过，就不允许执行删除。
- **进程占用时必须中止**，让用户退出 WorkBuddy；不要试图强删被占用的文件。
- **`--yes` 不等于授权**，它只省掉二次输入。真正的授权来自用户在对话里的明确同意。
- **默认带 `--backup`**，除非用户明确说不需要备份。
- **不要动 `workbuddy.db` 以外的系统文件**，也不要删 `skills/`、`connectors/`、`credentials/`。

## 坑

| 现象 | 说明 |
|---|---|
| 相对路径报「找不到脚本」 | 调用技能不切换 cwd，**必须用 `${CLAUDE_SKILL_DIR}` 或绝对路径**。见第一步。 |
| `Remove-Item` 报 `trash-failed` | 多半实际已删除。**删完必须重新列目录复查**，别只看报错。 |
| 界面列表还在 | 正常。清文件不会同步删列表；想连列表一起清用 `--purge-db`。 |
| 「莫明没删掉」 | WorkBuddy 没退干净，文件被占用。 |
| 当前会话的文件删不掉 | 正在使用中。退出后自动可删，不必单独处理。 |
| 清单里出现陌生文件夹名 | 该会话在数据库里没有标题，已退回显示项目文件夹名。 |

## 顺序铁律

**先在界面里删（打软删除标记），再跑脚本删文件。** 反过来会导致界面列表和磁盘状态对不上。
