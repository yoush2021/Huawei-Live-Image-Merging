#!/usr/bin/env python3
"""视频 → Live 图（Motion Photo）：ffmpeg 抽随机帧 + 原视频合成。
保留视频 EXIF/时间：输出文件 mtime 对齐视频 mtime；结果文件含 Motion Photo 双标准元数据。
用法: video_to_live.py <视频路径> <输出jpg路径> [--frame-pos 秒|random]
"""
import argparse, json, os, random, subprocess, sys
from pathlib import Path

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

def duration_of(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                          "-show_format", str(video)], capture_output=True, text=True, timeout=30)
    return float(json.loads(out.stdout).get("format", {}).get("duration", 0))

def extract_frame(video: Path, out_jpg: Path, dur: float, pos=None):
    """抽取一帧。pos 未给则随机抽（避开首尾 10%）。"""
    if pos is None:
        margin = max(0.2, dur * 0.1)
        lo, hi = margin, max(margin, dur - margin)
        pos = random.uniform(lo, hi) if hi > lo else dur / 2
    subprocess.run(
        ["ffmpeg", "-y", "-v", "quiet", "-ss", f"{pos:.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(out_jpg)],
        check=True, timeout=60)
    return pos

def build_motion_photo(jpg_bytes: bytes, video_bytes: bytes) -> bytes:
    xml = XMP_XML
    seg_payload = XMP_NS + xml
    seg = b"\xff\xe1" + (len(seg_payload) + 2).to_bytes(2, "big") + seg_payload
    assert jpg_bytes[:2] == b"\xff\xd8", "not a JPEG"
    new_jpg = jpg_bytes[:2] + seg + jpg_bytes[2:]
    offset = len(new_jpg)
    off_str = str(offset).rjust(12, "0")
    new_jpg = new_jpg.replace(b"____________", off_str.encode())
    return new_jpg + video_bytes

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out_jpg")
    ap.add_argument("--frame-pos", default="random", help="秒数或 random")
    args = ap.parse_args()

    video = Path(args.video)
    out_jpg = Path(args.out_jpg)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)

    dur = duration_of(video)
    tmp_frame = out_jpg.with_suffix(".frame.jpg")
    pos = None if args.frame_pos == "random" else float(args.frame_pos)
    extract_frame(video, tmp_frame, dur, pos)
    jpg = tmp_frame.read_bytes()
    vidb = video.read_bytes()
    data = build_motion_photo(jpg, vidb)
    out_jpg.write_bytes(data)
    os.remove(tmp_frame)
    # 保留视频时间戳
    st = video.stat()
    os.utime(out_jpg, (st.st_atime, st.st_mtime))
    print(f"{video.name}: dur={dur:.2f}s frame@{pos if pos is None else round(pos,2)}s → {out_jpg.name} "
          f"({len(data)/1024/1024:.1f}MB)")

if __name__ == "__main__":
    main()