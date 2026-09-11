# wb-conversation-cleanup

> 你在 WorkBuddy 里点了「删除对话」，它没有真的删除。
> 这个文件，直到你动手之前，一直躺在你的硬盘上。

一个用于**彻底清除 WorkBuddy 本地对话记录**的工具，可独立运行，也可作为 Agent Skill 被对话式触发。

---

## 一、需求从哪来

一个很具体的场景：某天翻了下自己的用户目录，发现 `~/.workbuddy/projects/` 下堆着十几个文件夹，打开一看是 `.jsonl` —— 逐行 JSON，明文，完整记录每一轮对话。

包括：

- 你说的每一句话
- 模型的思考过程
- **每一次工具调用的完整入参和返回结果**（文件路径、命令输出、API 响应）

然后发现一件更麻烦的事：**界面上已经删掉的对话，正文文件全都还在。**

实测情形：`projects/` 目录下堆着十几个文件夹，全部明文；其中若干条早已在界面里「删」过，正文依然完整保留。

---

## 二、为什么做这个

### 因为「删除」这个词在这个场景下具有误导性

在 WorkBuddy 界面删除会话，实际发生的事情是给数据库打个标记：

```sql
UPDATE sessions SET deleted_at = <时间戳> WHERE id = <会话UUID>
```

**仅此而已。** 列表里看不见了，文件一个没动。

这带来三个真问题：

| 问题 | 后果 |
|---|---|
| **隐私残留** | 你以为清掉了，别人打开文件夹就能看。含工具调用的完整上下文，信息密度远高于聊天文本本身 |
| **空间不释放** | 越积越多，`blobs`/`traces` 这类缓存目录增长更快，实测能占到上百 MB |
| **无法批量处理** | 手动找文件路径靠肉眼，会话 UUID 和对话标题之间没有可读的对应关系 |

### 因为现成的办法都不好用

- **手动删** —— 要先从 SQLite 里查 UUID，再对着文件名找，十几条能忍，几十条不行
- **重装软件** —— 为了清聊天记录重装，代价完全不成比例
- **第三方清理软件** —— 不知道它具体删了什么，反而更不可控

所以需要一个：**能看清删的是什么、能后悔、能批量**的工具。

---

## 三、原理

### 数据落在哪

| 内容 | 路径 |
|---|---|
| 对话正文 | `~/.workbuddy/projects/<项目ID>/<会话UUID>.jsonl` |
| 回滚快照 | 同目录 `<UUID>.file-rollback.ndjson` |
| 会话元信息 | 同目录 `<UUID>.meta.json` |
| 软删除标记 | `~/.workbuddy/workbuddy.db` → `sessions.deleted_at` |
| 附件缓存 | `~/.workbuddy/blobs`、`file-history`、`traces`、`artifact-index` |

### 工具做了什么

1. 读 `workbuddy.db`，拿到「哪些会话被标记删除」以及标题 —— 让 UUID 变回人能看懂的东西
2. 扫描 `projects/`，把 `<UUID>.jsonl` / `.file-rollback.ndjson` / `.meta.json` 三个文件归为一组
3. **先列清单给你看**，标好大小和标题
4. 确认后才删除，删完自动回收变空的项目文件夹

---

## 四、安装

### 作为 Skill（推荐，WorkBuddy / Claude Code 用户）

```bash
git clone https://github.com/DF-Guan/wb-conversation-cleanup.git \
    ~/.workbuddy/skills/wb-conversation-cleanup
```

装好后直接在对话里说：

> 清理对话 / 删除聊天记录 / 释放空间 / purge chat history

Agent 会自动加载并按流程执行。

### 作为独立脚本（不用 AI 工具的人）

```bash
git clone https://github.com/DF-Guan/wb-conversation-cleanup.git
cd wb-conversation-cleanup
python scripts/cleanup.py          # 弹出菜单，按提示操作
```

Windows 用户直接双击 `Run-Cleanup.bat`。

---

## 五、用法

### Skill 模式（对话触发，最省心）

你只管说需求，剩下的 Agent 来做：

```
你：帮我清理对话
Agent：先给你看清单 → 你要 xx MB 吗？
你：删
Agent：请先退出 WorkBuddy → 执行 → 报告释放了多少空间
```

### CLI 模式（想自己控制）

```bash
# 先看清单，什么都不删（这一步建议永远别跳过）
python scripts/cleanup.py --list

# 只清界面里已删除的那些，删前备份
python scripts/cleanup.py --soft-deleted --backup

# 清全部对话（--yes 跳过二次确认）
python scripts/cleanup.py --all --backup --yes

# 全部对话 + 缓存目录（blobs / file-history / traces / artifact-index）
python scripts/cleanup.py --all --include-cache --backup --yes
```

### 参数

| 参数 | 作用 |
|---|---|
| `--list` | 只预览，绝不删除 |
| `--soft-deleted` | 只处理 `deleted_at` 非空的会话 |
| `--all` | 处理所有会话 |
| `--include-cache` | 附带清理 blobs / file-history / traces / artifact-index |
| `--backup` | 删除前备份到 `~/Desktop/WB-Cleanup-Backup/<时间戳>/` |
| `--purge-db` | 额外清空数据库里的会话列表（会先备份数据库） |
| `--yes` | 跳过 `DELETE` 确认输入 |
| `--backup-dir PATH` | 自定义备份根目录 |

---

## 六、安全设计

删除是不可逆的，所以工具在几个地方刻意做得很保守：

1. **强制预览** —— Agent 模式下，SKILL.md 硬性规定：没有把清单给用户看过，不允许执行删除
2. **进程守护** —— 检测到 WorkBuddy 还在跑就不删（预览除外），避免删掉一半
3. **双重确认** —— 手动输入 `DELETE`，或 Agent 已在对话中征得同意后才用 `--yes`
4. **默认可备份** —— 备份开着才会后悔有门
5. **范围严格限定** —— 只碰 `projects/`、四个缓存目录和 `workbuddy.db`；不碰 `skills/`、`connectors/`、`credentials/`
6. **--purge-db 会先备份数据库** —— 包括 `.db` / `-wal` / `-shm` 三个文件

### 顺序很重要

> **先在界面里删（打软删除标记），再跑脚本删文件。**

反过来会导致数据库记录和磁盘状态对不上。`--soft-deleted` 模式依赖这个标记。

---

## 七、常见问题

**Q：删完界面列表还在？**
正常。删文件不会同步删列表，数据库记录独立存在。想连列表一起清，用 `--purge-db`。

**Q：`Remove-Item` 报 `trash-failed` 错？**
多半实际已经删掉了（Windows 上的回收站机制所致）。**删完重新列一遍目录确认**，别只看报错。

**Q：为什么有的条目没有标题？**
少数会话（如后台定时任务）在数据库里没有标题字段，工具会退回显示其所属的项目文件夹名。

**Q：当前正在聊的这个对话能删吗？**
不能，文件正被占用。退出 WorkBuddy 后自然可删，不需要特殊处理。

**Q：多久清一次合适？**
`blobs` 和 `traces` 增长最快，建议一个月左右 `--include-cache` 一次。

---

## 八、⚠️ 使用前请注意

**本工具会永久删除文件。**

- 第一次用建议选 `--list` 先看一遍清单
- 不确定就带上 `--backup`
- 数据无价，动手前请确认自己知道在删什么

作为 Skill 使用时请留意：Agent 拥有了删除你本地文件的能力。SKILL.md 中已写明「预览后方可删除」的硬约束，若你自行修改该约束，风险自负。

---

## 九、兼容性

| 平台 | 状态 |
|---|---|
| Windows | ✅ 已实测（含 NativeCommandError / 回收站行为） |
| macOS / Linux | ✅ 代码支持（`pgrep` 检测、`expanduser("~")` 路径），尚未实机测试 |

需要 Python 3.8+，无第三方依赖。

---

## License

MIT
