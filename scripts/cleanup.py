# -*- coding: utf-8 -*-
"""
WorkBuddy 本地数据彻底清理工具

用法：
  交互菜单     python cleanup.py
  预览         python cleanup.py --list [--cache-only] [--older-than 30d] [--top 10]
  只清缓存     python cleanup.py --cache-only --backup --yes
  软删除项     python cleanup.py --soft-deleted --backup --yes
  按时间       python cleanup.py --older-than 30d --backup --yes
  全清         python cleanup.py --all [--include-cache] --backup --yes
  还原         python cleanup.py --restore <备份目录>

原理：界面里的"删除"只是给 sessions.deleted_at 打标记（软删除），
    正文 .jsonl 仍留在磁盘上，必须手动删才真正清除。
"""
import os
import sys
import sqlite3
import shutil
import argparse
import datetime
import subprocess

ROOT = os.path.join(os.path.expanduser("~"), ".workbuddy")
PROJECTS = os.path.join(ROOT, "projects")
DBFILE = os.path.join(ROOT, "workbuddy.db")
EXTRA_DIRS = ["blobs", "file-history", "traces", "artifact-index"]
AUDIT_LOG = os.path.join(ROOT, "cleanup-audit.log")
LINE = "-" * 64


def hsize(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def default_backup_root():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    base = desktop if os.path.isdir(desktop) else os.path.expanduser("~")
    return os.path.join(base, "WB-Cleanup-Backup")


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
        out = subprocess.run(["pgrep", "-fl", "WorkBuddy"],
                             capture_output=True, text=True,
                             errors="ignore", timeout=20).stdout
        return bool(out.strip())
    except Exception:
        return False


def parse_age(s):
    """'30d' / '6m' / '1y' / '30' -> 天数"""
    s = (s or "").strip().lower()
    if not s:
        return None
    try:
        if s.endswith("d"):
            return int(s[:-1])
        if s.endswith("m"):
            return int(s[:-1]) * 30
        if s.endswith("y"):
            return int(s[:-1]) * 365
        return int(s)
    except ValueError:
        raise SystemExit("[参数错误] --older-than 格式应为 30d / 6m / 1y")


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


def _db_connect():
    return sqlite3.connect("file:%s?mode=ro" % DBFILE.replace("\\", "/"), uri=True)


def db_sessions():
    """返回 {session_id: (title, updated_ms)}"""
    meta = {}
    if not os.path.isfile(DBFILE):
        return meta
    conn = None
    try:
        conn = _db_connect()
        for sid, title, updated in conn.execute("select id, title, updated_at from sessions"):
            meta[sid] = (title, updated)
        for sid, deleted in conn.execute(
                "select id, deleted_at from sessions where deleted_at is not null"):
            meta.setdefault(sid, (None, deleted))
    except Exception as e:
        print("  [!] 读取数据库失败：%s" % e)
    finally:
        if conn:
            conn.close()
    return meta


def deleted_ids():
    ids = set()
    if not os.path.isfile(DBFILE):
        return ids
    conn = None
    try:
        conn = _db_connect()
        for row in conn.execute("select id from sessions where deleted_at is not null"):
            ids.add(row[0])
    except Exception:
        pass
    finally:
        if conn:
            conn.close()
    return ids


def item_time(sid, files, meta):
    """优先用数据库 updated_at，缺失时退回文件修改时间"""
    _title, updated = meta.get(sid, (None, None))
    if updated:
        try:
            return datetime.datetime.fromtimestamp(int(updated) / 1000)
        except (TypeError, ValueError):
            pass
    mtimes = []
    for fp, _s in files:
        try:
            mtimes.append(os.path.getmtime(fp))
        except OSError:
            pass
    return datetime.datetime.fromtimestamp(max(mtimes)) if mtimes else None


def build_plan(mode, idx, include_cache=False, older_days=None, top=None):
    """mode: 'soft' | 'all' | 'cache' | 'older' -> [(标签, [路径])]"""
    meta = db_sessions()
    now = datetime.datetime.now()
    plan = []

    if mode == "cache":
        pass
    elif mode == "soft":
        for sid in deleted_ids():
            files = idx.get(sid)
            if not files:
                continue
            t = item_time(sid, files, meta)
            when = t.strftime("%m-%d %H:%M") if t else "--"
            plan.append(("[%s] %s" % (when, (meta.get(sid, (None, None))[0] or "(无标题)")[:42]),
                         [fp for fp, _s in files]))
    else:
        for sid, files in idx.items():
            t = item_time(sid, files, meta)
            if mode == "older" and older_days is not None:
                if t is None or (now - t).days < older_days:
                    continue
            label = meta.get(sid, (None, None))[0]
            if not label:
                label = os.path.basename(os.path.dirname(files[0][0]))
            when = t.strftime("%m-%d") if t else "--"
            plan.append(("[%s] %s" % (when, label[:44]), [fp for fp, _s in files]))

    if include_cache or mode == "cache":
        for name in EXTRA_DIRS:
            d = os.path.join(ROOT, name)
            if os.path.isdir(d):
                plan.append(("[缓存目录] %s" % name, [d]))

    if top:
        plan.sort(key=lambda it: sum(path_size(p) for p in it[1]), reverse=True)
        plan = plan[:top]
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


def write_manifest(dst, files):
    """记录原始路径，供 --restore 还原"""
    try:
        with open(os.path.join(dst, "_manifest.txt"), "w", encoding="utf-8") as fh:
            fh.write("# 原始路径\t字节\n")
            for f in files:
                try:
                    fh.write("%s\t%d\n" % (f, os.path.getsize(f)))
                except OSError:
                    pass
    except OSError as e:
        print("  [!] 写入清单失败：%s" % e)


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
    write_manifest(dst, [f for f in files if os.path.isfile(f)])
    print("  备份 %d 个文件 -> %s" % (n, dst))
    return dst


def restore(src):
    """从备份目录还原，依赖其中的 _manifest.txt"""
    manifest = os.path.join(src, "_manifest.txt")
    if not os.path.isfile(manifest):
        print("[失败] 该目录缺少 _manifest.txt，无法确定原始路径：%s" % src)
        return 1
    ok = skip = fail = 0
    with open(manifest, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            orig = line.split("\t")[0]
            cached = os.path.join(src, os.path.basename(orig))
            if not os.path.isfile(cached) or os.path.exists(orig):
                skip += 1
                continue
            try:
                os.makedirs(os.path.dirname(orig), exist_ok=True)
                shutil.copy2(cached, orig)
                ok += 1
            except Exception as e:
                fail += 1
                print("  [!] 还原失败 %s -> %s" % (os.path.basename(orig), e))
    print("还原完成：成功 %d，跳过(已存在/缺失) %d，失败 %d" % (ok, skip, fail))
    return 0


def audit(text):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
    except OSError:
        pass


def do_delete(plan):
    freed = fails = 0
    for _label, paths in plan:
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


def cache_total():
    return sum(path_size(os.path.join(ROOT, d)) for d in EXTRA_DIRS)


def conv_summary(idx):
    return "%d 个会话，%s" % (len(idx), hsize(sum(s for fl in idx.values() for _p, s in fl)))


def run(mode, include_cache=False, want_backup=False, assume_yes=False,
        purge=False, backup_root=None, dry_run=False, older_days=None, top=None):
    backup_root = backup_root or default_backup_root()

    if not dry_run and app_running():
        print("[中止] WorkBuddy 仍在运行。请完全退出（含托盘图标）后再执行删除，")
        print("       否则文件被占用无法删除。")
        return 2

    idx = scan_projects()
    print("数据根目录：%s" % ROOT)
    print("对话占用：%s" % conv_summary(idx))
    print("缓存占用：%s\n" % hsize(cache_total()))

    plan = build_plan(mode, idx, include_cache, older_days, top)
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
        files = [p for _l, paths in plan for p in paths if os.path.isfile(p)]
        backups(files, "files", backup_root)

    if not assume_yes:
        if input("\n确认删除请输入 DELETE（区分大小写）：").strip() != "DELETE":
            print("输入不匹配，已取消。")
            return 0

    freed, fails = do_delete(plan)
    print("\n完成：释放 %s，失败 %d 项。" % (hsize(freed), fails))
    audit("模式=%s 删除 %d 项，释放 %s，失败 %d" % (mode, len(plan), hsize(freed), fails))
    print("审计日志：%s" % AUDIT_LOG)
    if purge:
        purge_db(backup_root)
    return 0


def list_backups(root):
    if not os.path.isdir(root):
        return []
    return sorted([os.path.join(root, d) for d in os.listdir(root)
                   if os.path.isdir(os.path.join(root, d))], key=os.path.getmtime)


def interactive():
    if app_running():
        print("\n  [!] WorkBuddy 正在运行，请先完全退出再运行本工具。")
        input("\n按回车键退出...")
        return
    idx = scan_projects()
    print(LINE)
    print(" WorkBuddy 对话清理工具")
    print(" " + ROOT)
    print(LINE)
    print("\n对话占用：%s" % conv_summary(idx))
    print("缓存占用：%s\n" % hsize(cache_total()))
    print("  1) 只看预览，不删除")
    print("  2) 只清理【界面里已删除】的对话")
    print("  3) 只清理【缓存】，保留所有对话")
    print("  4) 清理【全部】对话记录")
    print("  5) 清理【全部】对话 + 缓存")
    print("  6) 还原（restore）")
    print("  0) 退出")
    c = input("\n输入序号后回车：").strip()

    if c == "6":
        bks = list_backups(default_backup_root())
        if not bks:
            print("没有找到备份：%s" % default_backup_root())
        else:
            print("\n可用备份：")
            for i, b in enumerate(bks, 1):
                print("  %d) %s" % (i, os.path.basename(b)))
            try:
                restore(bks[int(input("选择序号：").strip()) - 1])
            except (ValueError, IndexError):
                print("无效选择。")
        input("\n按回车键退出...")
        return

    if c == "0":
        print("已取消。")
        return
    if c not in ("1", "2", "3", "4", "5"):
        print("无效选项。")
        return

    if c == "1":
        plan = build_plan("all", idx, True)
    else:
        mode, inc = {"2": ("soft", False), "3": ("cache", False),
                     "4": ("all", False), "5": ("all", True)}[c]
        plan = build_plan(mode, idx, inc)

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

    if input("\n是否先备份要删的文件？(y/N)：").strip().lower() == "y":
        files = [p for _l, paths in plan for p in paths if os.path.isfile(p)]
        backups(files, "files", default_backup_root())
    if input("\n确认删除请输入 DELETE：").strip() != "DELETE":
        print("输入不匹配，已取消。")
        return

    freed, fails = do_delete(plan)
    print("\n完成：释放 %s，失败 %d 项。" % (hsize(freed), fails))
    audit("模式=%s 删除 %d 项，释放 %s，失败 %d" % (c, len(plan), hsize(freed), fails))
    if c in ("4", "5"):
        if input("\n是否同时清空界面会话列表（数据库记录）？(y/N)：").strip().lower() == "y":
            purge_db(default_backup_root())
    print("\n可以重新启动 WorkBuddy 了。")
    input("\n按回车键退出...")


def main():
    ap = argparse.ArgumentParser(
        description="彻底清理 WorkBuddy 本地对话与缓存",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  cleanup.py --list --top 10\n"
               "  cleanup.py --cache-only --backup --yes\n"
               "  cleanup.py --older-than 30d --backup --yes\n"
               "  cleanup.py --restore ~/Desktop/WB-Cleanup-Backup/files-0911-092353\n")
    # 注意：--list 是"预览"修饰符而非模式，不能放进互斥组，
    # 否则 "--list --cache-only" 这类组合会被 argparse 拒绝。
    ap.add_argument("--list", action="store_true", help="只预览，不删除")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--cache-only", action="store_true", help="只清缓存，保留所有对话")
    g.add_argument("--soft-deleted", action="store_true", help="只清界面里已删除的对话")
    g.add_argument("--older-than", metavar="AGE", help="只清早于该时间的对话，如 30d / 6m / 1y")
    g.add_argument("--all", action="store_true", help="清理全部对话记录")
    g.add_argument("--restore", metavar="DIR", help="从备份目录还原")
    ap.add_argument("--include-cache", action="store_true",
                    help="连同 blobs/file-history/traces/artifact-index 一起清理")
    ap.add_argument("--top", type=int, metavar="N", help="只处理体积最大的 N 项")
    ap.add_argument("--backup", action="store_true", help="删除前先备份")
    ap.add_argument("--purge-db", action="store_true", help="同时清空数据库中的会话列表")
    ap.add_argument("--yes", action="store_true", help="跳过 DELETE 确认（仅限已确认过）")
    ap.add_argument("--backup-dir", default=None, help="备份存放根目录")
    args = ap.parse_args()

    if args.restore:
        sys.exit(restore(os.path.expanduser(args.restore)))

    if not any([args.list, args.cache_only, args.soft_deleted,
                args.all, args.older_than is not None]):
        interactive()
        return
    if args.older_than is not None:
        mode, days = "older", parse_age(args.older_than)
    elif args.cache_only:
        mode, days = "cache", None
    elif args.soft_deleted:
        mode, days = "soft", None
    else:
        mode, days = "all", None

    sys.exit(run(mode, args.include_cache, args.backup, True,
                 False, args.backup_dir, dry_run=args.list,
                 older_days=days, top=args.top) or 0)


if __name__ == "__main__":
    try:
        main()
    except EOFError:
        print("\n[非交互环境] 需要参数，例如：cleanup.py --list")
    except KeyboardInterrupt:
        print("\n已取消。")
