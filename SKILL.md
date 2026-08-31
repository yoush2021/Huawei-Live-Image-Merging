---
name: "restore-motion-photos"
description: "批量恢复动态照片：同名JPG+视频合成Google Motion Photo单文件，无损多线程本地转换"
---

# Skill: restore-motion-photos

批量恢复动态照片（Motion Photo）：把「同名 JPG/HEIC 主帧 + 短视频」对合并为可播放的动态照片；其余文件全量镜像到新目录。无损、多线程、纯本地处理。

## 背景

手机动态照片导出/转发后常被拆成两个文件：同名图片 + 视频。恢复方式按格式分流：
- **JPG/JPEG 主帧 + MP4/MOV/M4V** → 合成 Google Motion Photo 单文件 .jpg（视频字节原样追加 + 注入 XMP 元数据，100% 无损；华为图库/Google Photos/安卓相册可识别）
- **HEIC/HEIF 主帧 + MOV**（Apple Live Photo）→ HEIC 无法无损合成单文件，保持原生配对复制（主帧+视频+AAE 编辑记录）
- **AAE 文件**（Apple 编辑记录）→ 跟随主帧配对复制，支持 `IMG_Oxxxx.aae ↔ IMG_xxxx` 命名差异

**无损保证**：不改动任何图像/视频字节，只做字节拼接 + 元数据注入。视频部分与原文件逐字节一致（可 cmp 验证）。

## 判断标准

- 动态照片对：**同目录同名**的图片（.jpg/.jpeg/.heic/.heif）+ 视频（.mp4/.mov/.m4v），例如 `IMG_1.jpg + IMG_1.mp4`
- 已成型动态照：无配对视频的单文件 JPG 但内部已含 `ftyp`（已是 Motion Photo 单文件）→ 原样镜像
- 其余所有文件（普通照片/视频/AAE/杂项任意扩展名）→ 按原层级全量镜像复制，可整目录封存

## 脚本位置

仓库 `scripts/` 目录：`motion_photo_restore.py`（本项目即本 skill 的载体）
（纯 Python 标准库实现，无需 exiftool/ffmpeg；系统若有也可用于验证）

## 用法

```bash
python3 motion_photo_restore.py <输入目录> [选项]
```

| 选项 | 作用 |
|---|---|
| `--out <目录>` | 输出目录（默认：输入目录同级 `<名称>_restored`） |
| `--threads <N>` | 并发数（默认 CPU 核心数） |
| `--motion-only` | 只输出转换后的动态照片，不镜像其他文件 |
| `--force` | 覆盖输出目录中已存在的文件 |
| `--dry-run` | 只扫描统计，不写文件 |

**输出**：目录结构与输入完全一致（支持多级子目录），动态照片对在镜像中完成转换。

## 统计功能

运行后自动输出**层级统计表**（终端 + 输出目录 `转换报告.txt`）：

| 列 | 含义 |
|---|---|
| 可转换对 | 该层目录中可转换为动态照的同名 图+视频 对 |
| 已成型live | 已是 Motion Photo 单文件的图片 |
| 普通图 / 视频 / 杂项 | 无需转换的文件数 |
| 已转换 / 失败 | 输出侧的转换结果（便于与输入侧对比） |

`--dry-run` 可先预览输入侧统计而不写文件。

## 标准流程

1. **确认输入目录**：存在、可读；输出目录不能与输入目录相同
2. **预览**：`python3 motion_photo_restore.py <dir> --dry-run`（看层级统计）
3. **运行**：`python3 motion_photo_restore.py <dir> --threads 8`
4. **验证关键输出**（抽 1 个合成的 .jpg）：
   - 元数据：`exiftool -api largefilesupport=1 <out>.jpg` 应显示 `Motion Photo: 1`、`Micro Video: 1`
   - offset 精确性：元数据 `Micro Video Offset` 应等于文件中 `ftyp` 位置 - 4（脚本按定宽占位保证精确）
   - 视频无损（可选）：
     ```bash
     OFF=$(python3 -c "d=open('<out>.jpg','rb').read();print(d.rfind(b'ftyp')-4)")
     dd if=<out>.jpg of=/tmp/v.mp4 bs=1 skip=$OFF 2>/dev/null
     cmp -s /tmp/v.mp4 <原.mp4> && echo 无损
     ```
5. **汇报结果**：层级统计表 + 合成/HEIC配对保留/复制/失败数 + 输出目录路径

## 注意

- **私密图片**：全程本地处理，不联网、不上传、不调用图像分析 API
- **GPS 隐私**：JPG 通常带拍摄位置；如需外发，额外做脱敏副本（抹除 GPS/EXIF）
- 大目录（上千文件）建议 `--threads` 取 CPU 核心数，内存占用约每任务 2×文件大小
- 同名但扩展名大小写不同（`.JPG`/`.MP4`）也会匹配（按小写比对）
- 图片与视频必须在**同一目录**才算一对（避免跨目录同名误配）
- 输出保留输入目录的相对路径结构；输出目录会多一个 `转换报告.txt`

## 相关文件

- 本项目仓库即 skill 载体：`README.md`（完整文档）+ `scripts/`（全部脚本）