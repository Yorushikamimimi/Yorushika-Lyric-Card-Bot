# ヨルシカ · 留住这一帧

给夜鹿乐迷的本地壁纸与头像工房。用自己的 Live、MV 视频挑选真实画面，或让 GPT 根据官方角色参考制作简笔头像与插画壁纸。

日常使用就保持现在这样：**告诉本机当前GPT主控想新增什么 → GPT选片、制作并加入图库 → 刷新网页查看与下载**。这里的主控就是当前GPT会话，配合本机Python工具与网页使用，不需要外置Agent服务，也不要求用户手动整理元数据。

当前主线是本地视觉工房。旧歌词卡版本保留在 `codex/legacy-lyric-card` 分支（历史提交 `8d39bce`），停止维护；仓库名称暂时保留。

## 换模型 / 接手项目

先读 [AGENTS.md](AGENTS.md)，再读唯一进度入口 [CURRENT_PROGRESS.md](CURRENT_PROGRESS.md) 和 [选图标准与注意事项](VISUAL_GUIDE.md)。后者含已验证的校准图、选与不选的理由、设备裁切要求、素材增量流程。旧1080p MV候选已保留到历史并停用；用户更新的36首MV已重新实测、选帧入库，当前结果见进度。

## 两种用法

**在 Codex 里说需求**：项目已包含 `$yorushika-visuals` Skill。例如：

> 用 $yorushika-visuals 从四场 Live 里选六张有主唱、大屏和灯光的电脑壁纸，再给我两张适合手机的版本。

> 用 $yorushika-visuals 做一张 Elma 喝咖啡的简笔头像，参考官方周边，留白多一点。第二版只把杯子换成笔记本。

**在网页里选和调**：在项目目录运行：

```bash
python3 main.py
```

打开 <http://127.0.0.1:8765>。也可直接运行 `python3 studio.py --port 8765`。

- 视频壁纸：浏览精选 / Live / MV / 全部候选，选中后载入视频真实原帧，支持原图查看与下载；列表才用缩略图。调整尺寸、保留全图 / 裁满、焦点、曝光、对比度与锐化后导出 PNG。
- 手机默认铺满裁切。内置 iPhone 14 Plus 1284×2778、MacBook Pro 16 3456×2234、Windows 2K 2560×1440；也可自定义。MV 原画可选1024×1024截图头像，直接裁切、不用AI。
- 头像与插画：查看实际原图尺寸和来源，下载原图，或按方图 / 手机 / 电脑比例裁切；放大时会标出倍率。填写修改需求并复制给 Codex。
- 头像创作由 GPT 会话的**内置图像工具**执行。网页不直接调用生成模型，不要求 API 密钥；复制需求本身不会开始生成。

网页是本机服务；手机预设指输出壁纸尺寸，不代表已开放手机浏览器访问。导出文件全部保存在项目 `output/`，不修改原视频。

## 首次准备与依赖

需要 Python 3.10+、PATH 中可用的 `ffmpeg` / `ffprobe`，以及：

```bash
python3 -m pip install -r requirements.txt
```

默认只声明FastAPI、Uvicorn、Pydantic与Pillow四项运行依赖；视频工具另需已有的ffmpeg/ffprobe。网页使用普通HTML/CSS/JavaScript，不需要Node.js安装或前端构建。Playwright、Chromium、SlowAPI、歌词爬虫代理和Docker均不是当前运行要求。`requirements-studio.txt`保留为兼容入口，引用同一份默认清单；测试依赖单独放在 `requirements-dev.txt`。

本机已有依赖时无需重复安装。换机器后仅克隆源码会得到空图库，原视频、`output/`中的候选、元数据与成品需要另行保留或复制；GitHub不备份这些本地素材。日常抽帧和入库由当前GPT主控完成。下面的命令供主控维护时参考，首次为自己的目录建立候选：

```bash
python3 live_wallpaper.py sample \
  --input "/Users/yang/Movies/Bili23-Downloader/Yorushika_Live" \
  --per-chapter 1 --output output/my-first-look
```

每次使用新的输出目录，然后在网页刷新候选。每场优先匹配的 `[Chapters]` 版本，同一视频的符号链接不重复处理。抽样输出包括缩略图、带 ID 与时间的接触表、静态预览和来源清单。

MV 默认目录是 `/Users/yang/Movies/Bili23-Downloader/Yorushika_MV`，无章节可使用 `--count 16 --thumb-width 1280`。只处理完整视频，不把音频、封面或下载分片当作成片。

只列素材与章节时，用 `catalog`；指定时间、`--native`原帧、精细导出与 HDR 参数见 [视频工作流](.agents/skills/yorushika-visuals/references/live-workflow.md)。

## 图片与参考

以下成品链接属于本机 `output/`，在GitHub或只克隆源码的环境中不会提供图片；已有本机素材与成品继续保留。

- [最新作品总览](output/gallery-round4.html)：71张原图与设备版本，含本轮18张MV设备壁纸与3张截图头像；可离线打开下载。[第三轮](output/gallery-round3.html)保留。[第二轮](output/gallery-round2.html)与[第一轮](output/gallery.html)保留。日常仍使用上面的本地网页，离线总览是成品补充入口。
- `output/live-first-look/`、`output/live-second-look/`：75 + 98 张 Live 候选；`output/mv-refresh-20261003-v1/`：36首新MV、576张候选；旧80张在 `output/history/mv-first-look/`，当前网页停用。
- `output/wallpapers/`、`output/wallpapers-round2/`、`output/wallpapers-round3/`、`output/mv-wallpapers/`、`output/mv-wallpapers-round4/`：电脑、手机成品；每组保留 `original.png`、`wallpaper.png` 和 `provenance.json`。第三轮以 `exports-record.json` 的 `final_exports` 为准，拒选的23号仅保留作评审证据，不在总览和精选里。
- `output/avatars/`：5种GPT头像与6张MV截图头像，带来源；`output/illustrations/`：独立横竖构图插画原图；`output/illustration-wallpapers/`：设备尺寸版。
- `output/selection.json`：Agent 看图后推荐的候选；不是用户已接受的记录。
- `output/exports/`：网页壁纸导出；头像尺寸导出位置以网页产物为准。
- [官方角色与周边参考](.agents/skills/yorushika-visuals/references/visual-sources.md)：Amy / Elma、Hitchcock、suis 涂鸦及证据边界。

生成物和本地路径记录默认不进 Git。复制项目源码到另一台机器后，需要重新指定素材并抽样，或另行复制自己的 `output/`。

## 画面处理的边界

四场当前素材是 4096×1890、BT.2020 / HLG。工具将其映射到可查看的 SDR / sRGB，保留舞台摄影、原片内已有文字；不另烧字幕，也不让 AI 补脸或造歌词。转换以 1000 nit 参考显示峰值为默认假设，不等同于专业母版校色或 Dolby Vision 动态处理。

锐化只能轻调边缘，放大只是插值，不能恢复运动模糊或创造真实细节。第二轮旧5首MV实际为1920×1080；第四轮新36首实测编码3840×2160、BT.709 SDR，部分源仍有修复放大/软边痕迹，不保证原生4K细节。新成品逐张标明裁切和插值倍率，文件名中的「4K」不作为画质证据。GPT本轮实际返回1254方图、853×1844竖图、1672×941横图；更大的设备版属于插值。

横屏转手机可能裁掉人物、大屏或日文，因此要看导出后的实际画面。`cover` 不会新增补边，但不会自动识别和删除原片中烘焙的黑条（如本地《春泥棒》MV）；需换帧或通过聊天要求精确裁掉原片边框。网页的实时色调预览是近似效果，以导出的 PNG 为准。

GPT头像是原创粉丝二创；MV截图头像标为真实原画裁切，均不是官方发布的头像产品。“嘻嘻可可”目前暂列为可能指《ヒッチコック》，待用户确认。

## 验证与项目状态

```bash
python3 -m unittest discover -s tests -v
```

运行测试前按需使用 `python3 -m pip install -r requirements-dev.txt`；本机已具备依赖时不重复安装。测试使用临时合成视频，不依赖或修改用户的 Live。真实素材、页面操作与尚未验证项见 [CURRENT_PROGRESS.md](CURRENT_PROGRESS.md)。

项目已将 `main.py` 默认入口转到本地工房。早期的 `crawler.py`、`card_maker.py`、抓词实验和Docker文件原样保留作历史参考，不在当前启动、安装与测试流程中；旧 `GET /card` 已退役。根目录旧Docker方案不适用于当前工房，完整旧代码与旧依赖以历史分支为准。当前仅支持本机启动，不增加部署入口。
