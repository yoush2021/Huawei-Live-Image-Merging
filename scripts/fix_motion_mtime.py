#!/usr/bin/env python3
"""修复合成 Motion Photo 文件的文件系统时间戳（从源同名 JPG 恢复 mtime/atime）。
用法: python3 fix_motion_mtime.py <输出目录> <源目录> [--threads N]
"""
import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def fix_one(out_file: Path, src_map):
    src = src_map.get(out_file.name.lower())
    if src is None:
        return (out_file.name, "no-source")
    try:
        st = src.stat()
        os.utime(out_file, (st.st_atime, st.st_mtime))
        return (out_file.name, "fixed")
    except OSError as e:
        return (out_file.name, f"err:{e}")


def main():
    ap = argparse.ArgumentParser(description="恢复合成 Motion Photo 的源时间戳")
    ap.add_argument("out_dir", help="输出目录（m60p 等）")
    ap.add_argument("src_dir", help="源目录（同名 JPG 所在）")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 8)
    args = ap.parse_args()

    out_root = Path(args.out_dir).resolve()
    src_root = Path(args.src_dir).resolve()

    # 预建源文件索引（文件名小写 → 路径）
    src_map = {}
    for p in src_root.rglob("*"):
        if p.is_file():
            src_map.setdefault(p.name.lower(), p)

    # 找出所有合成文件（含 MotionPhoto XMP 标记）
    targets = []
    for p in out_root.rglob("*"):
        if p.suffix.lower() not in (".jpg", ".jpeg"):
            continue
        try:
            with open(p, "rb") as f:
                data = f.read(2 * 1024 * 1024)  # 只读前 2MB（XMP 段在文件头附近）
            if b"<GCamera:MotionPhoto>1" in data:
                targets.append(p)
        except OSError:
            continue

    print(f"待修复合成文件: {len(targets)}（源索引 {len(src_map)} 条）")

    stats = {"fixed": 0, "no-source": 0, "err": 0}
    total = len(targets)
    if total == 0:
        print("没有需要修复的文件")
        return
    done = 0
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futs = [pool.submit(fix_one, p, src_map) for p in targets]
        for fut in as_completed(futs):
            done += 1
            _, status = fut.result()
            stats[status if status in stats else "err"] += 1
            if done % 200 == 0 or done == total:
                print(f"  进度 {done}/{total}  fixed={stats['fixed']} "
                      f"no-source={stats['no-source']} err={stats['err']}")

    print(f"\n完成：修复 {stats['fixed']}，无源文件 {stats['no-source']}，错误 {stats['err']}")


if __name__ == "__main__":
    main()