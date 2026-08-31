# Huawei-Live-Image-Merging
华为live图合并

这个脚本的由来，由于我不喜欢云备份，又遇到了huawei emui升级harmony5-7，期间拍摄的live图，通过 windows pc 通过数据线拷贝到电脑本地，遇到的问题，这是个修补方案。

## 事故原委：
 1. 手机通过数据线连接 `windows pc`
 2. 手机弹出`传输数据`/`传输照片`/`仅充电`，选择前两个都行
 3. `windows pc` 打开文件管理器，会出现你的手机型号，双击打开
 4. 找到`相册或者相机`，直接拷贝到电脑桌面上
 5. 此时打开电脑桌面上的备份相册，会感觉天都塌了。
 6. 文件里并没有动态live图，有的是`IMG_001.jpg(heic)` 和 `IMG_001.mp4(mov)`
 7. 此时再备份到飞牛NAS也不会识别为live图。

*此时如果你和上面操作一样，别灰心本脚本可以助你找回live图，实测了apple、huawei mate40pro、mate60pro、mate60prop、mate80promax相册的图片文件，都可无损恢复，恢复出来的数据和原目录一致，
只操作合并，其他非live文件则直接拷贝至输出目录，不用挑挑捡捡的翻照片，原目录可直接封存

# 批量恢复动态照片（Motion Photo Restore）

把手机动态照片导出/转发后被拆散的两个文件——**同名图片 + 短视频**——无损合并回可播放的 Motion Photo 单文件。

纯 Python 标准库实现，**无第三方依赖**（不依赖 exiftool / ffmpeg），多线程、纯本地处理，不改动任何图像/视频字节。

---

## 解决的问题

手机（华为/小米/荣耀/苹果等）的「动态照片 / Live Photo」在导出、微信/QQ 传输、NAS 备份、第三方图床后，经常被拆成两个文件：

```
IMG_0017.JPG   ← 静态主帧
IMG_0017.mov   ← 动态视频（1~15 秒）
```

相册里只剩一张静态图，动态效果丢了。本脚本把这些同名配对**原样合并**回 Google Motion Photo 单文件（`.jpg`），华为图库 / Google Photos / 安卓相册/Flickr 等支持 Motion Photo 的查看器即可直接播放动态效果。

## 特性

- ✅ **100% 无损**：视频字节原样追加在 JPG 尾部，可 `cmp` 逐字节验证与原视频一致；图像部分只插入一段 XMP 元数据
- ✅ **Google Motion Photo 双标准**：同时注入 `MotionPhoto` 与 `MicroVideo` 两套 XMP 元数据，兼容性最好（Google Photos、华为图库、安卓相册均识别）
- ✅ **多线程**：默认使用全部 CPU 核心，大目录也快
- ✅ **目录结构保持**：输出与输入目录层级完全一致，可直接封存替换
- ✅ **纯本地**：不联网、不上传、不调用任何云 API，私密照片不出本机
- ✅ **零依赖**：只要系统有 Python 3.8+ 即可运行

## 支持格式

| 输入主帧 | 输入视频 | 处理方式 |
|---|---|---|
| `.jpg` / `.jpeg` | `.mp4` / `.mov` / `.m4v` | **合并**为 Google Motion Photo 单文件 `.jpg` |
| `.heic` / `.heif` | `.mov`（Apple Live Photo） | **保留原生配对**复制（HEIC 无法无损合成单文件） |
| 已含视频的单文件 JPG | — | 检测为已成型动态照，原样镜像 |
| 其他任意文件 | — | 全量镜像复制，不丢任何东西 |

> .aae（Apple 编辑记录）会跟随主帧配对复制，支持 `IMG_O0232.aae ↔ IMG_0232.HEIC` 命名差异。

## 用法

```bash
python3 motion_photo_restore.py <输入目录> [选项]
```

### 选项

| 选项 | 作用 |
|---|---|
| `--out <目录>` | 输出目录（默认：输入目录同级 `<名称>_restored`） |
| `--threads <N>` | 并发数（默认：CPU 核心数） |
| `--motion-only` | 只输出转换后的动态照片，不镜像其他文件 |
| `--force` | 覆盖输出目录中已存在的文件 |
| `--dry-run` | 只扫描统计，不写文件（先预览再动手） |

### 典型流程

```bash
# 1. 先预览（只统计，不写文件）
python3 motion_photo_restore.py /path/to/photos --dry-run

# 2. 正式转换
python3 motion_photo_restore.py /path/to/photos --threads 8

# 3. 转换完成后输出目录多一个 转换报告.txt（层级统计）
```

输出目录结构：

```
photos_restored/
├── IMG_0017.jpg        ← 合成后的 Motion Photo（可直接播放动态）
├── IMG_0232.HEIC       ← HEIC 配对保留
├── IMG_0232.mov
├── IMG_O0232.aae
├── 普通照片.jpg
└── 转换报告.txt
```

## 原理（无损合并）

```
新 JPG = 原 JPG 前 2 字节(SOI) + XMP APP1 段 + 原 JPG 其余字节 + 原视频全部字节
```

1. 构造一段 XMP 元数据（Google 的 `http://ns.google.com/photos/1.0/camera/` 命名空间），声明 `MotionPhoto=1`、`MicroVideo=1`，并给出视频在文件中的偏移量
2. 把 XMP 段作为标准 JPEG APP1 段插在 SOI 之后——只插入、不改动原图像的任何段
3. 把视频字节完整追加到文件尾部，偏移量 = 新 JPG 总长（定宽 12 位数字写回，保证段长度恒定、偏移精确）

播放器（华为图库/Google Photos）读 XMP 知道文件尾有视频，直接从偏移处读取并作为动态照片播放。视频字节与原始视频**逐字节一致**，因此绝对无损。

## 验证合成结果

```bash
# 1. 元数据应显示 Motion Photo
exiftool -api largefilesupport=1 输出.jpg
#    →
#    Motion Photo                 : 1
#    Micro Video                  : 1

# 2. 视频无损验证（抽 1 个合成的 jpg）
OFF=$(python3 -c "d=open('输出.jpg','rb').read();print(d.rfind(b'ftyp')-4)")
dd if=输出.jpg of=/tmp/v.mp4 bs=1 skip=$OFF 2>/dev/null
cmp -s /tmp/v.mp4 原视频.mp4 && echo "无损 ✓"
```

> 偏移量 = 文件中 `ftyp`（MP4 开头标识）出现位置 - 4，因为 XMP 里 `MicroVideoOffset` 指的就是视频数据起始位置。

## 辅助脚本（同一仓库）

| 脚本 | 功能 | 依赖 |
|---|---|---|
| `video_to_live.py` | 单个视频 → Live 图：ffmpeg 抽一帧做主图 + 原视频合成 | ffmpeg/ffprobe |
| `batch_video_to_live.py` | 批量视频 → Live 图（读 JSON 清单，多线程） | ffmpeg/ffprobe |
| `fix_motion_mtime.py` | 修复合成文件的文件系统时间戳（从源同名 JPG 恢复 mtime/atime） | 无 |

> `motion_photo_restore.py` 面向「已有 图+视频 配对」的恢复；`video_to_live.py` 系面向「只有视频想变成 Live 图」的创作，两者共享同一套 XMP 合成逻辑。

## 注意事项

- 图片与视频必须**在同一目录**才算一对（避免跨目录同名误配）
- 同名但扩展名大小写不同（`.JPG`/`.MP4`）也会匹配（按小写比对）
- 输入目录一路只读，脚本永远不会写入输入目录
- 大目录（上千文件）内存占用约每任务 2×文件大小，`--threads` 别超过内存余量
- **GPS 隐私**：JPG 通常带拍摄位置；如需外发，请额外做脱敏副本（抹除 GPS/EXIF）

## License

MIT
