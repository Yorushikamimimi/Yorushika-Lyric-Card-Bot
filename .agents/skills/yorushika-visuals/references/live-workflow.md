# Live / MV 选片与导出

从仓库根目录执行。依赖 Python / Pillow、ffmpeg 和 ffprobe。所有命令只读源视频，输出目录必须是新目录；错误时保留现场并换新版本名，不清空已有成果。

## 找片

```bash
python3 live_wallpaper.py catalog --input "/path/to/Yorushika_Live" --output output/catalog-v1
python3 live_wallpaper.py sample --input "/path/to/Yorushika_Live" --per-chapter 1 --output output/sample-v1
```

`catalog` 记录分辨率、显示比例、色彩和章节。`sample` 每个章节取指定数量的点；没有章节时才用 `--count` 均匀采样。优先带 Chapters 的同名文件，解析符号链接后去重。

MV 目录为 `/Users/yang/Movies/Bili23-Downloader/Yorushika_MV`。先确认完整视频可以解码，忽略 `.m4s`、音轨与封面。可用 `--count 16 --thumb-width 1280` 做每首初筛，之后围绕角色动作或背景补抽。记录字幕是否已烧进原片；不编造、补写或用 AI 去改原画文字。

`manifest.json` 记录候选 ID、源视频、秒数、章节、相对缩略图路径。打开 `contact_sheets/*.jpg` 找方向，再看大预览 / 原帧。`index.html` 是静态候选页，交互调参用 `studio.py`。

围绕已选时间补抽：

```bash
python3 live_wallpaper.py sample --input "/path/to/selected [Chapters].mp4" \
  --timestamps "00:08:53,00:08:55,00:08:57" --thumb-width 1280 --output output/detail-v1
```

清晰度分数可能来自密集文字或噪点，不能代替看图。人物演唱姿态、主体位置、歌词可读性和现场感分别判断。抽样不是完整浏览四场演唱会。

## 导出

```bash
python3 live_wallpaper.py render \
  --source "/path/to/selected [Chapters].mp4" --timestamp "00:08:55" \
  --size 3840x2160 --fit contain --sharpen 0.15 --output output/desktop-v1

python3 live_wallpaper.py render \
  --source "/path/to/selected [Chapters].mp4" --timestamp "00:08:55" \
  --size 1284x2778 --fit cover --focus-x 0.65 --sharpen 0.15 --output output/phone-v1

python3 live_wallpaper.py render \
  --source "/path/to/selected.mp4" --timestamp "00:08:55" \
  --native --sharpen 0 --output output/native-frame-v1
```

时间和焦点只是语法例子，不是通用最优参数。导出后查看实图再决定是否保留。

手机默认 cover 铺满；原片自带黑白边时也要换帧或明确裁掉，不能仅靠 cover 承诺无边。MacBook Pro 16 用3456×2234，Windows 2K用2560×1440，iPhone 14 Plus用1284×2778。MV头像可用1024×1024 cover，逐张保证脸部与动作完整。

注意：网页/Skill的手机默认策略不等于CLI默认值；CLI `render` 默认仍是contain，手机版必须显式传 `--fit cover`。CLI没有任意像素框裁切或自动去原片黑条参数；需要精确crop时由Agent另做确定性处理并记录原帧坐标。

| 参数 | 用途 |
|---|---|
| `--size WxH` | 精确像素；优先上方已确认设备规格 |
| `--native` | 按源显示尺寸输出，用于高清主预览；不从缩略图放大 |
| `--fit contain` | 完整保留画面并补边，适合整行日文或大舞台 |
| `--fit cover` | 填满并裁切，适合主体集中的竖屏 |
| `--focus-x/y 0…1` | 裁切位置；contain 时控制画面在留白里的位置。0 左 / 上，1 右 / 下 |
| `--background 0x101216` | 补边颜色，默认深色 |
| `--exposure -2…2` | sRGB 显示域近似曝光档位，不是 RAW 线性调色 |
| `--contrast` | 默认 1；少量调整，避免压死暗部 |
| `--sharpen` | 0 关闭；首次建议 0.15–0.25，避免字边白晕 |
| `--hdr-mode auto` | 按元数据识别 HLG / PQ；只在已知标签错误时强制指定 |
| `--hdr-peak` | 默认 1000 nit 参考显示假设，不是实测母版亮度 |

每次生成 `original.png`（源栅格解码帧，HDR 已转 SDR，未加锐化）、`wallpaper.png` 和 `provenance.json`（来源、请求时间、参数、转换、是否插值放大）。时间是解码请求点，不宣称精确帧 PTS。非方形像素在成品中按显示比例校正。

## HDR 处理

当前四场均为 HLG / BT.2020。此环境 ffmpeg 无 zscale，因此使用 YCbCr→RGB、BT.2100 HLG / ST2084 PQ 解码、BT.2020→BT.709 矩阵、Hable 亮度映射和 sRGB 编码构成的 3D LUT；缩略图 33³，成品 65³。输出 PNG 明确标记 sRGB / BT.709 / full range。

参考：[ITU-R BT.2100-2](https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.2100-2-201807-S!!PDF-E.pdf)、[FFmpeg tonemap 源码](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/vf_tonemap.c)。这是可查看的 SDR 转换，不应用 Dolby Vision 动态元数据，超出 sRGB 色域会裁切。不能承诺与任意 HDR 显示器观感完全相同。

## 将推荐放入网页

在 `output/selection.json` 记录看过图的候选，`reviewed` 指 Agent 看图，不代表用户接受：

```json
{"items":[{"manifest":"live-first-look/manifest.json","candidate_id":"V03-C003","title":"彩色舞台","note":"主唱位、大屏与全场灯光同时入画，保留全幅更合适。","reviewed":true,"recommended_for":["desktop"]}]}
```

`manifest` 是相对 `output/` 的清单路径；实际 ID 以该清单为准。不同批次的短 ID 可能重复，必须同时记录 manifest；旧的无 manifest 推荐只有全库短 ID 唯一时生效。若当前帧不足以满足要求，对邻近时间补抽。

## Agent更新后，用户只需刷新网页

以下整理由Agent完成，不让用户手动执行：

1. 候选清单放在 `output/<批次>/manifest.json`（一级子目录），网页不会任意递归发现深层manifest。复用已有未选候选则不用创建重复manifest。
2. CLI默认不区分Live/MV。新MV批次需给manifest顶层写入 `"source_kind": "mv"`，并将各video的label设为真实本地曲目名；否则网页默认归为Live。按当前进度先遵守MV暂缓决定。
3. 对看过原帧的候选追加 `output/selection.json`，保持旧顺序；`manifest + candidate_id`为长期定位，网页内部opaque ID不要写入长期记录。手机推荐在note写清焦点与取舍，`recommended_for`只是推荐标签，不会自动应用裁切参数。
4. 成品用新的 `output/wallpapers-<批次>/<作品-设备>/`（MV同理）保存，检查原帧、PNG与provenance。预先导出的PNG不会自动变成网页历史成品列表；用户刷新后看到新增精选，可在现有页面选设备并导出下载。这是当前日常工作流，不需要另做App。
5. 若需要附带全部已导出PNG的离线总览，单独维护当前总览；旧 `output/build_round2_gallery.py` 是固定目录/数字的本地批次脚本，不是通用自动索引。新增目录后要明确纳入并检查，不能盲重跑当作已更新。
6. 确认现有本机服务正常；若没启动则由Agent启动现有入口，不杀死未知进程。刷新 `/api/library` 与实际页面，检查新增项、顺序、原帧与一次目标设备导出。素材更新本身无需重启服务，也不安装新客户端。

素材版本以真实路径、file_size、mtime_ns、时长、尺寸与批次对应，见 [VISUAL_GUIDE](../../../../VISUAL_GUIDE.md)。当前工具只去同inode别名和同目录同名Chapters版本，不会自动找最高画质，也没有跨批次内容去重。文件内容变化但大小/mtime碰巧相同，缓存不会可靠识别；同路径替换源文件需要重新catalog、抽样、看图，不得把旧缩略图视为新4K成果。
