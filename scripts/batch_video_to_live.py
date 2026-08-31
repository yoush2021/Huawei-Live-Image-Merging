#!/usr/bin/env python3
"""批量把视频转 Live 图（Motion Photo）：随机帧主图 + 原视频合成。
用法: batch_video_to_live.py <清单json> <输出根目录> [--threads N]
清单格式: [{"path": "...", "duration": 3.0}, ...] （与 /tmp/video_list.json 一致）
输出: <输出根目录>/<相对路径>/<视频名>.jpg（mtime 对齐源视频）
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from video_to_live import build_motion_photo, duration_of, extract_frame


def convert_one(item, out_root):
    src = Path(item["path"])
    rel = Path(item.get("rel", src.name))
    out = out_root / rel.with_suffix(".jpg")
    if out.exists():
        return ("skip", str(out))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        dur = item.get("duration") or duration_of(src)
        tmp = out.with_suffix(".frame.jpg")
        extract_frame(src, tmp, dur, None)
        jpg = tmp.read_bytes()
        vidb = src.read_bytes()
        data = build_motion_photo(jpg, vidb)
        tmp.unlink(missing_ok=True)
        out.write_bytes(data)
        st = src.stat()
        os.utime(out, (st.st_atime, st.st_mtime))
        return ("ok", str(out))
    except Exception as e:
        return ("fail", f"{src.name}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("list_json")
    ap.add_argument("out_root")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 8)
    args = ap.parse_args()

    items = json.load(open(args.list_json))
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"待转换视频: {len(items)} → {out_root}")
    stats = {"ok": 0, "skip": 0, "fail": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futs = [pool.submit(convert_one, it, out_root) for it in items]
        for fut in as_completed(futs):
            done += 1
            kind, _ = fut.result()
            stats[kind] += 1
            if done % 25 == 0 or done == len(items):
                print(f"  进度 {done}/{len(items)}  ok={stats['ok']} "
                      f"skip={stats['skip']} fail={stats['fail']}")
    print(f"\n完成: ok={stats['ok']} skip={stats['skip']} fail={stats['fail']}")
    print(f"输出: {out_root}")


if __name__ == "__main__":
    main()