#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量恢复动态照片（Motion Photo）— 无损 / 多线程 / 纯本地
=============================================================
支持两种动态照片格式：
 1) 华为/安卓式：同名 JPG 主帧 + 短视频（mp4/mov）
    → 合成 Google Motion Photo 单文件 .jpg（华为图库 / Google Photos / 安卓相册可识别）
    → 把视频字节原样追加到 JPG 尾 + 注入 XMP 元数据，100% 无损
 2) 苹果 Live Photo：同名 HEIC/HEIF 主帧 + MOV 视频
    → Apple 原生结构本就是主帧+视频配对，无需合成；无损复制两边到输出目录
    （HEIC 无法无损转为 Motion Photo 单文件，保持原生配对即无损恢复）

用法：
    python3 motion_photo_restore.py <输入目录> [选项]

行为：
  - 动态照片对（同名 图+视频）→ 转换为动态照片单文件或 HEIC 配对保留
  - 其余所有文件（普通照片/视频/已成型动态照/任意杂项）→ 按原目录层级全量镜像复制
  - 输出目录结构与输入完全一致，可直接封存旧目录

选项：
    --out <目录>        输出目录（默认：输入目录同级 <名称>_restored）
    --threads <N>       并发数（默认：CPU 核心数）
    --motion-only       只输出合成的动态照片，普通文件不复制
    --force             覆盖输出目录中已存在的文件
    --dry-run           只扫描统计，不写文件

示例：
    python3 motion_photo_restore.py /path/to/photos --threads 8
"""
import argparse
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
AAE_EXTS = {".aae"}

# XMP 元数据模板（Google Motion Photo 双标准：MotionPhoto + MicroVideo）
# __OFFSET__ 占位为 12 个下划线，替换为定宽 12 位数字，保证段长度恒定、偏移精确
XMP_NS = b"http://ns.google.com/photos/1.0/camera/"
XMP_XML = (
    b"<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>"
    b"<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='XMP Core 5.1.2'>"
    b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
    b"<rdf:Description rdf:about='' "
    b"xmlns:GCamera='http://ns.google.com/photos/1.0/camera/'>"
    b"<GCamera:MotionPhoto>1</GCamera:MotionPhoto>"
    b"<GCamera:MotionPhotoVersion>1</GCamera:MotionPhotoVersion>"
    b"<GCamera:MotionPhotoPresentationTimestampUs>0</GCamera:MotionPhotoPresentationTimestampUs>"
    b"<GCamera:MicroVideo>1</GCamera:MicroVideo>"
    b"<GCamera:MicroVideoOffset>____________</GCamera:MicroVideoOffset>"
    b"</rdf:Description></rdf:RDF></x:xmpmeta>"
    b"<?xpacket end='w'?>"
)


def build_motion_photo(jpg_bytes: bytes, video_bytes: bytes) -> bytes:
    """将视频无损并入 JPG，返回完整 Motion Photo 字节。"""
    # 1) 构造 XMP APP1 段（插在 SOI 之后，不改动原有段）
    xml = XMP_XML
    seg_payload = XMP_NS + xml
    seg = b"\xff\xe1" + (len(seg_payload) + 2).to_bytes(2, "big") + seg_payload
    # 2) 新 JPG = SOI + XMP 段 + 原数据
    assert jpg_bytes[:2] == b"\xff\xd8", "not a JPEG (SOI missing)"
    new_jpg = jpg_bytes[:2] + seg + jpg_bytes[2:]
    # 3) 视频偏移 = 新 JPG 总长（视频紧跟其后）
    offset = len(new_jpg)
    off_str = str(offset).rjust(12, "0")
    new_jpg = new_jpg.replace(b"____________", off_str.encode())
    # 4) 追加视频
    return new_jpg + video_bytes


def _aae_matches(aae_stem: str, img_stem: str) -> bool:
    """AAE 与主帧的配对判定：同名，或 AAE 多带 'O' 前缀（IMG_O0232 ↔ IMG_0232）。"""
    if aae_stem == img_stem:
        return True
    # 备份工具常见命名：IMG_O0232.aae ↔ IMG_0232.HEIC
    return img_stem == aae_stem.replace("IMG_O", "IMG_", 1)


def _detect_already_live(path: Path) -> bool:
    """单文件 JPG/JPEG 已内嵌视频（已是 Motion Photo 单文件）→ True。"""
    if path.suffix.lower() not in (".jpg", ".jpeg"):
        return False
    try:
        with open(path, "rb") as f:
            return b"ftyp" in f.read()
    except OSError:
        return False


def scan_pairs(root: Path):
    """扫描目录：返回 (动态照片三元素组列表, 普通文件列表)。
    三元素组 = (图片, 视频, [关联AAE])。同名图片+视频视为动态照片。
    其余所有文件（普通照片/视频/已成型动态照/任意杂项）为普通文件，全量镜像输出。"""
    images, videos, aaes = {}, {}, {}
    ordinary_all = []  # 所有非图片/视频/AAE 文件（txt、md、bin，任意扩展名）
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXTS:
            images.setdefault(p.stem.lower(), []).append(p)
        elif ext in VIDEO_EXTS:
            videos.setdefault(p.stem.lower(), []).append(p)
        elif ext in AAE_EXTS:
            aaes.setdefault(p.stem.lower(), []).append(p)
        else:
            ordinary_all.append(p)

    pairs = []
    paired_files = set()
    # 按目录分组，避免跨目录同名误配
    by_dir = {}
    for stem in sorted(set(images) | set(videos)):
        for f in images.get(stem, []) + videos.get(stem, []):
            by_dir.setdefault(f.parent, []).append(f)
    for dirpath, files in by_dir.items():
        imgs = [f for f in files if f.suffix.lower() in IMAGE_EXTS]
        vids = [f for f in files if f.suffix.lower() in VIDEO_EXTS]
        stems = {f.stem.lower() for f in files}
        for img in imgs:
            for vid in vids:
                if img.stem.lower() != vid.stem.lower():
                    continue
                aae_group = []
                for aae_stem, aae_files in aaes.items():
                    if _aae_matches(aae_stem, img.stem.lower()):
                        aae_group.extend(aae_files)
                pairs.append((img, vid, aae_group))
                paired_files.add(img)
                paired_files.add(vid)
                paired_files.update(aae_group)
        _ = stems

    # 普通文件 = 所有未被配对的文件（含已成型单文件动态照、普通照片/视频、杂项）
    ordinary = [p for p in ordinary_all if p not in paired_files]
    for mapping in (images, videos, aaes):
        for stem, files in mapping.items():
            ordinary.extend(f for f in files if f not in paired_files)
    return pairs, ordinary


def collect_stats(root: Path, pairs, ordinary):
    """按目录层级统计：可转换对 / 已成型live / 普通图 / 普通视频 / 杂项。
    返回 {相对目录字符串: {pairs,live,img,vid,other}}，含根节点。"""
    stats = {}

    def bucket(rel):
        return stats.setdefault(str(rel) if str(rel) != "." else "(根目录)",
                                {"pairs": 0, "live": 0, "img": 0, "vid": 0, "other": 0})

    for img, vid, aae in pairs:
        try:
            rel = img.parent.relative_to(root)
        except Exception:
            rel = Path(".")
        bucket(rel)["pairs"] += 1
    for f in ordinary:
        try:
            rel = f.parent.relative_to(root)
        except Exception:
            rel = Path(".")
        b = bucket(rel)
        ext = f.suffix.lower()
        if ext in IMAGE_EXTS and _detect_already_live(f):
            b["live"] += 1
        elif ext in IMAGE_EXTS:
            b["img"] += 1
        elif ext in VIDEO_EXTS:
            b["vid"] += 1
        else:
            b["other"] += 1
    return stats


def worker(pair, out_root, force):
    img, vid, aae_files = pair
    # 保留相对路径
    try:
        rel_dir = img.parent.relative_to(args_root)
    except Exception:
        rel_dir = Path(".")
    out_file = out_root / rel_dir / (img.stem + img.suffix.lower())
    if out_file.exists() and not force:
        return ("skip", str(out_file))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    # 复制关联 AAE（保持原名与路径）
    copied_aae = []
    for aae in aae_files:
        dst = out_root / rel_dir / aae.name
        if not dst.exists() or force:
            shutil.copy2(aae, dst)
        copied_aae.append(dst.name)
    # JPG/JPEG → 合成 Motion Photo 单文件（无损）
    if img.suffix.lower() in (".jpg", ".jpeg"):
        jpg = img.read_bytes()
        vid_b = vid.read_bytes()
        data = build_motion_photo(jpg, vid_b)
        out_file.write_bytes(data)
        # 保留源文件的文件系统时间戳（mtime/atime），避免变成今天
        try:
            st = img.stat()
            os.utime(out_file, (st.st_atime, st.st_mtime))
        except OSError:
            pass
        return ("ok", str(out_file), copied_aae)
    # HEIC/HEIF → Apple Live Photo 原生结构：主帧+视频本就配对，无损复制两边
    shutil.copy2(img, out_file)
    shutil.copy2(vid, out_root / rel_dir / (vid.stem + vid.suffix.lower()))
    return ("pair-copy", str(out_file), copied_aae)


def copy_file(src, out_root, force):
    try:
        rel = src.relative_to(args_root)
    except Exception:
        rel = Path(src.name)
    out_file = out_root / rel
    if out_file.exists() and not force:
        return ("skip", str(out_file))
    if out_file.exists() and out_file.samefile(src):
        return ("skip-same", str(out_file))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out_file)
    return ("copy", str(out_file))


def print_stats_table(stats, results=None):
    """打印层级统计表。results 为 None 时是输入侧，否则为输出侧汇总。"""
    header = f"{'层级':<32}{'可转换对':>8}{'已成型live':>10}{'普通图':>8}{'视频':>8}{'杂项':>8}"
    if results:
        header += f"{'已转换':>8}{'失败':>6}"
    print(header)
    print("-" * len(header))
    titles = {"(根目录)": "(根目录)"}
    for rel in sorted(stats):
        s = stats[rel]
        row = f"{rel:<32}{s['pairs']:>8}{s['live']:>10}{s['img']:>8}{s['vid']:>8}{s['other']:>8}"
        if results:
            r = results.get(rel, {})
            row += f"{r.get('ok', 0) + r.get('pair', 0):>8}{r.get('fail', 0):>6}"
        print(row)
    # 汇总行
    tot = {"pairs": 0, "live": 0, "img": 0, "vid": 0, "other": 0}
    for s in stats.values():
        for k in tot:
            tot[k] += s[k]
    row = f"{'合计':<32}{tot['pairs']:>8}{tot['live']:>10}{tot['img']:>8}{tot['vid']:>8}{tot['other']:>8}"
    if results:
        to = {"ok": 0, "pair": 0, "fail": 0}
        for r in results.values():
            for k in to:
                to[k] += r.get(k, 0)
        row += f"{to['ok'] + to['pair']:>8}{to['fail']:>6}"
    print(row)


def main():
    global args_root
    ap = argparse.ArgumentParser(description="批量恢复动态照片（无损 / 多线程 / 本地）")
    ap.add_argument("input", help="输入目录（含照片与视频）")
    ap.add_argument("--out", help="输出目录（默认: <输入>_restored）")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4,
                    help="并发数（默认 CPU 核心数）")
    ap.add_argument("--motion-only", action="store_true",
                    help="只输出转换后的动态照片，不镜像其他文件")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的输出文件")
    ap.add_argument("--dry-run", action="store_true", help="只扫描统计，不写文件")
    args = ap.parse_args()

    args_root = Path(args.input).resolve()
    if not args_root.is_dir():
        sys.exit(f"错误：输入目录不存在：{args_root}")

    out_root = (Path(args.out).resolve() if args.out
                else args_root.parent / (args_root.name + "_restored"))
    if out_root == args_root:
        sys.exit("错误：输出目录不能与输入目录相同")

    # 输入目录只读保护：脚本对输入只做读取，绝不写入
    if not os.access(args_root, os.R_OK):
        sys.exit(f"错误：输入目录不可读：{args_root}")

    # 输出可写性检查：默认输出建在输入目录同级，若父目录只读（如只读挂载）则报错并要求 --out
    if args.dry_run:
        pass  # dry-run 不写任何文件，无需检查
    else:
        out_parent = out_root.parent
        if out_root.exists():
            if not os.access(out_root, os.W_OK | os.X_OK):
                sys.exit(f"错误：输出目录存在但不可写：{out_root}\n"
                         f"提示：原始目录可能为只读挂载，请用 --out 指定可写位置，\n"
                         f"      例如 --out {os.path.expanduser('~/openclaw-restored')}")
        elif not os.access(out_parent, os.W_OK):
            sys.exit(f"错误：无法在只读位置创建输出目录：{out_root}\n"
                     f"提示：原始目录可能为只读挂载（输出默认建在其同级）。\n"
                     f"      请用 --out 指定可写位置，例如 --out {os.path.expanduser('~/openclaw-restored')}")

    pairs, ordinary = scan_pairs(args_root)
    stats_in = collect_stats(args_root, pairs, ordinary)
    print(f"输入目录   : {args_root}（只读）")
    print(f"输出目录   : {out_root}")
    print(f"动态照片对 : {len(pairs)} 对")
    print(f"镜像文件   : {len(ordinary)} 个（含普通照片/视频/杂项）")
    print()
    print("═════ 输入侧统计（原始目录层级） ═════")
    print_stats_table(stats_in)
    print()
    if args.dry_run:
        for img, vid, aae in pairs[:20]:
            print(f"  [对] {img.name} + {vid.name}"
                  + (f" + AAE:{'/'.join(a.name for a in aae)}" if aae else ""))
        if len(pairs) > 20:
            print(f"  ... 其余 {len(pairs) - 20} 对省略")
        print("--dry-run 完成，未写任何文件")
        return

    tasks = [(img, vid, aae) for img, vid, aae in pairs]
    if not args.motion_only:
        tasks = tasks + [("copy", f) for f in ordinary]

    results = {"ok": 0, "skip": 0, "copy": 0, "pair-copy": 0, "fail": 0}
    total = len(tasks)
    done = 0
    # 输出侧按层级汇总
    stats_out = {rel: {"ok": 0, "pair": 0, "fail": 0}
                 for rel in stats_in}
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = {}
        for t in tasks:
            if t[0] == "copy":
                futures[pool.submit(copy_file, t[1], out_root, args.force)] = t
            else:
                futures[pool.submit(worker, t, out_root, args.force)] = t
        for fut in as_completed(futures):
            done += 1
            tag = futures[fut]
            try:
                kind, path = fut.result()[:2]
            except Exception as e:
                kind, path = "fail", f"{tag[0] if isinstance(tag, tuple) else tag}: {e}"
                results["fail"] += 1
            else:
                results[kind] = results.get(kind, 0) + 1
                if isinstance(tag, tuple) and kind in ("ok", "pair-copy", "fail"):
                    img = tag[1]
                    try:
                        rel = str(img.parent.relative_to(args_root))
                    except Exception:
                        rel = "(根目录)"
                    if rel == ".":
                        rel = "(根目录)"
                    if kind == "ok":
                        stats_out.setdefault(rel, {"ok": 0, "pair": 0, "fail": 0})["ok"] += 1
                    elif kind == "pair-copy":
                        stats_out.setdefault(rel, {"ok": 0, "pair": 0, "fail": 0})["pair"] += 1
                    else:
                        stats_out.setdefault(rel, {"ok": 0, "pair": 0, "fail": 0})["fail"] += 1
            if done % 10 == 0 or done == total:
                print(f"  进度 {done}/{total}  （ok={results['ok']} "
                      f"copy={results['copy']} pair-copy={results['pair-copy']} "
                      f"skip={results['skip']} fail={results['fail']}）")

    print(f"\n完成：合成 {results['ok']}，HEIC配对保留 {results['pair-copy']}，"
          f"复制 {results['copy']}，跳过 {results['skip']}，失败 {results['fail']}")
    print()
    print("═════ 输出侧统计（转换后） ═════")
    print_stats_table(stats_in, stats_out)
    if results["fail"]:
        print("⚠️  有失败项，请检查上面的错误信息")
    print(f"输出目录：{out_root}")

    # 落一份报告到输出目录
    try:
        report = out_root / "转换报告.txt"
        with open(report, "w", encoding="utf-8") as f:
            f.write(f"输入目录: {args_root}\n")
            f.write(f"输出目录: {out_root}\n")
            f.write(f"转换时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write("=== 输入侧统计 ===\n")
            f.write(_stats_to_text(stats_in))
            f.write("\n=== 输出侧统计 ===\n")
            f.write(_stats_to_text(stats_in, stats_out))
            f.write(f"\n合成 {results['ok']}，HEIC配对保留 {results['pair-copy']}，"
                    f"复制 {results['copy']}，失败 {results['fail']}\n")
        print(f"报告已写入: {report}")
    except OSError as e:
        print(f"（报告写入失败: {e}）")


def _stats_to_text(stats_in, stats_out=None):
    lines = [f"{'层级':<32}{'可转换对':>8}{'已成型live':>10}{'普通图':>8}{'视频':>8}{'杂项':>8}"]
    for rel in sorted(stats_in):
        s = stats_in[rel]
        row = f"{rel:<32}{s['pairs']:>8}{s['live']:>10}{s['img']:>8}{s['vid']:>8}{s['other']:>8}"
        if stats_out:
            r = stats_out.get(rel, {})
            row += f"  → 转换{ r.get('ok',0)+r.get('pair',0) } 失败{r.get('fail',0)}"
        lines.append(row)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()