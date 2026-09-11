# -*- coding: utf-8 -*-
"""
WorkBuddy 本地数据彻底清理工具

支持两种用法：
  1) 交互菜单：   python cleanup.py
  2) 命令行/Agent：python cleanup.py --list
                   python cleanup.py --soft-deleted --backup --yes
                   python cleanup.py --all --include-cache --backup --yes

原理：界面里的"删除"只是给 sessions.deleted_at 打标记（软删除），
    正文 .jsonl 文件仍留在磁盘上，必须手动删才真正清除。
"""
import os
import sys
import glob
import sqlite3
import shutil
import argparse
import datetime
import subprocess

ROOT = os.path.join(os.path.expanduser("~"), ".workbuddy")
PROJECTS = os.path.join(ROOT, "projects")
DBFILE = os.path.join(ROOT, "workbuddy.db")
EXTRA_DIRS = ["blobs", "file-history", "traces", "artifact-index"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LINE = "-" * 64


def hsize(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def default_backup_root():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return os.path.join(desktop if os.path.isdir(desktop) else os.path.expanduser("~"),
                        "WB-Cleanup-Backup")


def path_size(p):
    if os.path.isfile(p):
        return os.path.getsize(p)
    total = 0
    if os.path.isdir(p):
        for r, _, fs in os.walk(p):
            for f in fs:
                try:
                    total += os.path.getsize(os.path.join(r, f))
                except OSError:
                    pass
    return total


def app_running():
    """检测 WorkBuddy 是否还在运行（运行时文件被占用，删不掉）"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True,
                                 errors="ignore", timeout=20).stdout
            return "WorkBuddy" in out
        else:
            out = subprocess.run(["pgrep", "-fl", "WorkBuddy"],
                                 capture_output=True, text=True,
                                 errors="ignore", timeout=20).stdout
            return bool(out.strip())
    except Exception:
        return False


def scan_projects():
    """返回 {session_id: [(路径, 字节)]}"""
    idx = {}
    if not os.path.isdir(PROJECTS):
        return idx
    for d in os.listdir(PROJECTS):
        p = os.path.join(PROJECTS, d)
        if not os.path.isdir(p):
            continue
        for f in os.listdir(p):
            fp = os.path.join(p, f)
            if not os.path.isfile(fp):
                continue
            sid = None
            if f.endswith(".file-rollback.ndjson"):
                sid = f[:-len(".file-rollback.ndjson")]
            elif f.endswith(".meta.json"):
                sid = f[:-len(".meta.json")]
            elif f.endswith(".jsonl"):
                sid = f[:-len(".jsonl")]
            if sid is None:
                continue
            idx.setdefault(sid, []).append((fp, path_size(fp)))
    return idx


def db_rows(only_deleted):
    if not os.path.isfile(DBFILE):
        return []
    conn = None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % DBFILE.replace("\\", "/"), uri=True)
        sql = "select id, title, deleted_at from sessions"
        if only_deleted:
            sql += " where deleted_at is not null"
        rows = conn.execute(sql).fetchall()
        return rows
    except Exception as e:
        print("  [!] 读取数据库失败：%s" % e)
        return []
    finally:
        if conn:
            conn.close()


def build_plan(mode, idx, include_cache=False):
    """mode: 'soft' | 'all' -> [(标签, [路径])]"""
    plan = []
    if mode == "soft":
        for sid, title, ts in db_rows(only_deleted=True):
            files = idx.get(sid)
            if not files:
                continue
            when = ""
            if ts:
                when = datetime.datetime.fromtimestamp(int(ts) / 1000).strftime("%m-%d %H:%M")
            plan.append(("[%s] %s" % (when, (title or "(无标题)")[:42]),
                         [fp for fp, _ in files]))
    else:
        titles = {sid: t for sid, t, _ in db_rows(only_deleted=False)}
        for sid, files in idx.items():
            label = titles.get(sid) or ""
            if not label:
                # 数据库里没有标题时，退回用项目文件夹名，避免清单里出现无法辨认的条目
                label = os.path.basename(os.path.dirname(files[0][0]))
            plan.append((label[:48], [fp for fp, _ in files]))
    if include_cache:
        for name in EXTRA_DIRS:
            d = os.path.join(ROOT, name)
            if os.path.isdir(d):
                plan.append(("[缓存目录] %s" % name, [d]))
    return plan


def show_plan(plan):
    total = 0
    for label, paths in plan:
        size = sum(path_size(p) for p in paths)
        total += size
        print("  %-54s %s" % (label[:54], hsize(size)))
    print(LINE)
    print("  合计 %d 项，%s" % (len(plan), hsize(total)))
    return total


def backups(files, tag, dest_root):
    dst = os.path.join(dest_root, "%s-%s" % (tag, datetime.datetime.now().strftime("%m%d-%H%M%S")))
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in files:
        if os.path.isfile(f):
            try:
                shutil.copy2(f, os.path.join(dst, os.path.basename(f)))
                n += 1
            except Exception as e:
                print("  [!] 备份失败 %s -> %s" % (f, e))
    print("  备份 %d 个文件 -> %s" % (n, dst))
    return dst


def do_delete(plan):
    freed, fails = 0, 0
    for _, paths in plan:
        for p in paths:
            try:
                freed += path_size(p)
                if os.path.isfile(p):
                    os.remove(p)
                elif os.path.isdir(p):
                    shutil.rmtree(p)
            except Exception as e:
                fails += 1
                print("  [!] 删除失败 %s -> %s" % (p, e))
    if os.path.isdir(PROJECTS):
        for d in os.listdir(PROJECTS):
            p = os.path.join(PROJECTS, d)
            if os.path.isdir(p) and not os.listdir(p):
                try:
                    os.rmdir(p)
                except OSError:
                    pass
    return freed, fails


def purge_db(backup_root):
    stamp = datetime.datetime.now().strftime("%m%d-%H%M%S")
    dst = backups([DBFILE, DBFILE + "-wal", DBFILE + "-shm"], "db", backup_root)
    try:
        conn = sqlite3.connect(DBFILE)
        conn.execute("delete from sessions")
        try:
            conn.execute("delete from session_usage")
        except Exception:
            pass
        conn.commit()
        conn.execute("vacuum")
        conn.close()
        print("  已清空界面会话列表，数据库原档备份于：%s" % dst)
    except Exception as e:
        print("  [!] 清空数据库失败：%s" % e)


def run(mode, include_cache, want_backup, assume_yes, purge, backup_root=None, dry_run=False):
    backup_root = backup_root or default_backup_root()
    # 预览只是读文件，不需要独占；只有真正删除才要求退出 WorkBuddy
    if not dry_run and app_running():
        print("[中止] WorkBuddy 仍在运行。请完全退出（含托盘图标）后再执行删除，")
        print("       否则文件被占用无法删除。")
        return 2
    idx = scan_projects()
    print("数据根目录：%s" % ROOT)
    print("磁盘上现有对话：%d 个会话，%s\n" % (len(idx), hsize(sum(
        s for fl in idx.values() for _, s in fl))))

    plan = build_plan(mode, idx, include_cache)
    if not plan:
        print("没有匹配的内容。")
        return 0

    print(LINE)
    print("以下内容将被永久删除：")
    print(LINE)
    show_plan(plan)

    if dry_run:
        print("\n[预览模式] 未删除任何内容。")
        return 0

    if want_backup:
        files = [p for _, paths in plan for p in paths if os.path.isfile(p)]
        backups(files, "files", backup_root)

    if not assume_yes:
        ans = input("\n确认删除请输入 DELETE（区分大小写）：").strip()
        if ans != "DELETE":
            print("输入不匹配，已取消。")
            return 0

    freed, fails = do_delete(plan)
    print("\n完成：释放 %s，失败 %d 项。" % (hsize(freed), fails))
    if purge:
        purge_db(backup_root)
    return 0


def interactive():
    idx = scan_projects()
    print(LINE)
    print(" WorkBuddy 对话清理工具")
    print(" " + ROOT)
    print(LINE)
    if app_running():
        print("\n  [!] WorkBuddy 正在运行，请先完全退出再运行本工具。")
        input("\n按回车键退出...")
        return
    print("\n磁盘上现有对话：%d 个会话，%s\n" % (len(idx), hsize(sum(
        s for fl in idx.values() for _, s in fl))))
    print("  1) 只看预览，不删除")
    print("  2) 只清理【界面里已删除】的对话")
    print("  3) 清理【全部】对话记录")
    print("  4) 清理【全部】对话 + 附件缓存")
    print("  0) 退出")
    c = input("\n输入序号后回车：").strip()
    if c == "0":
        print("已取消。")
        return
    if c not in ("1", "2", "3", "4"):
        print("无效选项。")
        return
    plan = build_plan("soft" if c == "2" else "all", idx, c == "4")
    if not plan:
        print("\n没有匹配的内容。")
        input("\n按回车键退出...")
        return
    print("\n" + LINE)
    show_plan(plan)
    if c == "1":
        print("\n预览模式，未删除任何内容。")
        input("\n按回车键退出...")
        return
    b = input("\n是否先备份要删的文件？(y/N)：").strip().lower()
    if input("\n确认删除请输入 DELETE：").strip() != "DELETE":
        print("输入不匹配，已取消。")
        return
    freed, fails = do_delete(plan)
    print("\n完成：释放 %s，失败 %d 项。" % (hsize(freed), fails))
    if c in ("3", "4"):
        if input("\n是否同时清空界面会话列表（数据库记录）？(y/N)：").strip().lower() == "y":
            purge_db(default_backup_root())
    print("\n可以重新启动 WorkBuddy 了。")
    input("\n按回车键退出...")


def main():
    ap = argparse.ArgumentParser(description="彻底清理 WorkBuddy 本地对话与缓存")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="只预览，不删除")
    g.add_argument("--soft-deleted", action="store_true", help="只清理界面里已删除的对话")
    g.add_argument("--all", action="store_true", help="清理全部对话记录")
    ap.add_argument("--include-cache", action="store_true",
                    help="连同 blobs/file-history/traces/artifact-index 一起清理")
    ap.add_argument("--backup", action="store_true", help="删除前先备份")
    ap.add_argument("--purge-db", action="store_true", help="同时清空数据库中的会话列表")
    ap.add_argument("--yes", action="store_true", help="跳过 DELETE 确认（仅限已在对话中确认过）")
    ap.add_argument("--backup-dir", default=None, help="备份存放根目录")
    args = ap.parse_args()

    if not (args.list or args.soft_deleted or args.all):
        interactive()
        return
    mode = "soft" if args.soft_deleted else "all"
    sys.exit(run(mode, args.include_cache, args.backup,
                 True, False, args.backup_dir, dry_run=args.list) or 0)


if __name__ == "__main__":
    try:
        main()
    except EOFError:
        print("\n[非交互环境] 需要参数，例如：cleanup.py --list")
    except KeyboardInterrupt:
        print("\n已取消。")
