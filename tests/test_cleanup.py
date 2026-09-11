# -*- coding: utf-8 -*-
"""
无第三方依赖的自测。运行：

    python tests/test_cleanup.py

也可用 pytest 跑（函数名符合其约定）。
"""
import os
import sys
import shutil
import sqlite3
import tempfile
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import cleanup


def make_fake_env(files=None, deleted_ids=(), extra_dirs=True):
    """造一个假的 ~/.workbuddy 环境并重定向模块常量"""
    tmp = tempfile.mkdtemp(prefix="wbclean_test_")
    root = os.path.join(tmp, ".workbuddy")
    projects = os.path.join(root, "projects")
    db = os.path.join(root, "workbuddy.db")
    os.makedirs(projects)

    now = datetime.datetime.now().timestamp()
    layout = files or {}
    for folder, blobs in layout.items():
        d = os.path.join(projects, folder)
        os.makedirs(d, exist_ok=True)
        for name, size in blobs.items():
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(b"x" * size)

    conn = sqlite3.connect(db)
    conn.execute("create table sessions (id text, title text, updated_at integer, deleted_at integer)")
    for sid, deleted in deleted_ids:
        conn.execute("insert into sessions values (?,?,?,?)",
                     (sid, "T-" + sid[:4], int(now * 1000), int(now * 1000) if deleted else None))
    conn.commit()
    conn.close()

    cache_created = []
    if extra_dirs:
        for name in cleanup.EXTRA_DIRS:
            d = os.path.join(root, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "cache.bin"), "wb") as fh:
                fh.write(b"c" * 1000)
            cache_created.append(d)

    cleanup.ROOT = root
    cleanup.PROJECTS = projects
    cleanup.DBFILE = db
    cleanup.AUDIT_LOG = os.path.join(tmp, "audit.log")
    return tmp, root, projects, cache_created


def test_scan_and_plan_all():
    tmp, _root, _proj, _c = make_fake_env(
        {"p1": {"aaa.jsonl": 100, "aaa.meta.json": 10},
         "p2": {"bbb.jsonl": 200}},
        deleted_ids=[("aaa", False), ("bbb", False)])
    try:
        idx = cleanup.scan_projects()
        assert len(idx) == 2, idx
        assert "aaa" in idx and "bbb" in idx
        plan = cleanup.build_plan("all", idx)
        labels = [l for l, _p in plan]
        assert any("T-bbb" in l for l in labels), labels
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plan_falls_back_to_folder_name_without_title():
    """数据库里没有标题时，应退回显示项目文件夹名，而不是留空"""
    tmp, _root, _proj, _c = make_fake_env({"demo-project": {"aaa.jsonl": 100}})
    try:
        idx = cleanup.scan_projects()
        labels = [l for l, _p in cleanup.build_plan("all", idx)]
        assert any("demo-project" in l for l in labels), labels
        assert "(无标题)" not in labels[0], labels[0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_only_does_not_touch_conversations():
    tmp, _root, _proj, cache_dirs = make_fake_env({
        "p1": {"aaa.jsonl": 100},
    })
    try:
        idx = cleanup.scan_projects()
        plan = cleanup.build_plan("cache", idx)
        paths = [p for _l, ps in plan for p in ps]
        for d in cache_dirs:
            assert d in paths, "缓存目录应入选"
        assert not any("aaa.jsonl" in p for p in paths), "cache-only 绝不能包含对话正文"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_older_than_filters():
    tmp, _root, _proj, _c = make_fake_env({
        "p1": {"aaa.jsonl": 100},
        "p2": {"bbb.jsonl": 100},
    })
    try:
        idx = cleanup.scan_projects()
        # 文件都是刚建的 => older-than 1d 不应命中
        assert cleanup.build_plan("older", idx, older_days=1) == []
        # older-than 0d 应全部命中
        assert len(cleanup.build_plan("older", idx, older_days=0)) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_top_n_sorts_by_size_desc():
    tmp, _root, _proj, _c = make_fake_env(
        {"p1": {"aaa.jsonl": 100}, "p2": {"bbb.jsonl": 900}},
        deleted_ids=[("aaa", False), ("bbb", False)])
    try:
        idx = cleanup.scan_projects()
        plan = cleanup.build_plan("all", idx, top=1)
        assert len(plan) == 1
        assert "T-bbb" in plan[0][0], plan[0][0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_backup_manifest_and_restore_roundtrip():
    tmp, _root, proj, _c = make_fake_env({
        "p1": {"aaa.jsonl": 512, "aaa.meta.json": 20},
    })
    try:
        idx = cleanup.scan_projects()
        target = os.path.join(proj, "p1", "aaa.jsonl")
        backup_root = os.path.join(tmp, "bk")
        dst = cleanup.backups([target], "files", backup_root)
        assert os.path.isfile(os.path.join(dst, "_manifest.txt"))
        content = open(os.path.join(dst, "_manifest.txt"), encoding="utf-8").read()
        assert target in content, "清单必须记录原始绝对路径"

        os.remove(target)
        assert not os.path.exists(target)
        rc = cleanup.restore(dst)
        assert rc == 0
        assert os.path.isfile(target), "还原后文件应回到原路径"
        assert os.path.getsize(target) == 512
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_removes_files_and_empty_dirs():
    tmp, _root, proj, _c = make_fake_env({
        "p1": {"aaa.jsonl": 100, "aaa.file-rollback.ndjson": 50, "aaa.meta.json": 10},
    })
    try:
        idx = cleanup.scan_projects()
        plan = cleanup.build_plan("all", idx)
        freed, fails = cleanup.do_delete(plan)
        assert fails == 0
        assert freed == 160, freed
        assert not os.path.exists(os.path.join(proj, "p1")), "空目录应被回收"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_age():
    assert cleanup.parse_age("30d") == 30
    assert cleanup.parse_age("2m") == 60
    assert cleanup.parse_age("1y") == 365
    assert cleanup.parse_age("45") == 45


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("  PASS  " + fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL  %s -> %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1
            print("  ERROR %s -> %s" % (fn.__name__, e))
    print("-" * 50)
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
