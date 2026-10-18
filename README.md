# ヨルシカ · 留住这一帧

给自己用的夜鹿壁纸、头像与插画工房。用本地 Live / MV 留下喜欢的真实画面，也可以让 GPT 参考官方人物和周边制作粉丝二创。

**告诉有本地文件访问和命令执行能力的 GPT Agent 想新增什么 → Agent 选片、制作、加入图库 → 刷新本机网页，查看和下载。**

## 界面预览

选择画面，调整电脑或手机尺寸，再导出 PNG。

![视频壁纸工作台](docs/screenshots/wallpaper-workbench.png)

<details>
<summary>展开查看 Live / MV 精选</summary>

Live 的舞台、大屏与演唱剪影，以及 MV 中适合留下的场景。

![Live 精选](docs/screenshots/live-picks.png)
![Live 与 MV 精选](docs/screenshots/live-and-mv-picks.png)
![MV 精选](docs/screenshots/mv-picks.png)

</details>

<details>
<summary>展开查看头像与插画</summary>

既有 GPT 制作的手绘二创，也有直接裁切 MV 原画的截图头像。

![头像与插画工作台](docs/screenshots/avatar-workbench.png)
![MV 截图头像](docs/screenshots/mv-avatars.png)

</details>

截图来自现有本机图库，用于展示界面；源码仓库不包含完整图库和原视频。

## 可以做什么

- 浏览精选、全部候选、Live 和 MV；选中后预览真实原帧，也可下载原图。
- 先选 Live 按场次查找，或选 MV 按曲目查找；切到精选或收藏时保留当前来源，点「全部候选」恢复全部画面。
- 点击画面标题右侧的心形收藏，在“收藏”中集中查看；收藏保存在本机，刷新或重启后仍保留。
- 制作电脑、手机壁纸和方形头像，调整裁切位置与画面明暗。手机默认铺满裁切。
- 调好取景后点“留作比较”，可并排查看最多 3 个方案；点选喜欢的方案，再导出 PNG。方案仅临时保留，换图、换尺寸或刷新后清空。
- 查看头像与插画的实际尺寸、来源，下载原图或重新裁切。
- 把创作或修改需求交给当前 GPT，由内置图像工具完成二创，再加入图库。

支持 iPhone 14 Plus、MacBook Pro 16、Windows 2K 等尺寸，也可自定义。网页在本机运行，手机选项指壁纸尺寸。

## 怎么用

需要 Python 3.10+，以及可用的 `ffmpeg` / `ffprobe`。首次准备：

```bash
python3 -m pip install -r requirements.txt
python3 main.py
```

打开 <http://127.0.0.1:8765>。已有依赖时直接启动即可，也可使用 `python3 studio.py`。

### 首次导入自己的视频

新克隆的仓库没有图库，也没有网页上传入口。把可解码的 Live / MV 视频放在本机文件夹，再向**能读取该文件夹并执行本机命令的 Agent**（例如已授权目录访问的 Codex）提供完整路径。普通网页版 GPT 聊天不能直接读取你的本机路径。

可以直接告诉 Agent：

> 第一次建库，请读取我提供的本地视频目录，按项目视频工作流核验并抽样，实际看图筛选后入库；保留原视频和旧记录，完成后告诉我刷新网页。

Agent 使用已有的 `catalog` / `sample` 工具，最短命令及 Live / MV 入库区别见[视频工作流：首次建库](.agents/skills/yorushika-visuals/references/live-workflow.md#首次建库)。候选入库后刷新网页即可查看；人工精选还需要 Agent 实际看图，不会因抽样自动产生。你不需要手动编辑清单。

### 日常使用

日常新增素材直接在 GPT 会话里说明，例如：

> 从 Live 里选几张有主唱剪影、大屏日文和漂亮灯光的壁纸，电脑和手机分别选构图。

> 参考官方 Elma 周边，做一张喝咖啡的简笔头像，留白多一点。

项目包含 `$yorushika-visuals` Skill，供当前 GPT 按既定标准制作。网页负责查看、调整和下载，图片创作由 GPT 会话执行。

默认 Python 依赖只有 FastAPI、Uvicorn、Pydantic、Pillow。前端使用普通 HTML / CSS / JavaScript，无需前端构建或外置 Agent 服务。

## 素材与画质

- 视频、候选、成品和来源记录保存在本机；`output/` 不上传 GitHub。换机器仅克隆源码会得到空图库，需要另行复制素材与 `output/`。
- Live / MV 截图保留真实画面，不用 AI 补脸、补字或造细节。放大属于插值，不能恢复模糊。
- GPT 作品标明参考和二创来源，属于非官方粉丝作品。
- 网页可导出当前选择；预先调好的设备版本可看本机 `output/gallery-round6.html`（五场 Live 新增成品），此前成品仍保留在 `output/gallery-round5.html` 和 `output/gallery-round4.html`。

## 继续维护

- [当前进度](CURRENT_PROGRESS.md)：最新素材、成品、验证和待办。
- [视觉指南](VISUAL_GUIDE.md)：选图样例、画质要求和设备裁切标准。
- [接手说明](AGENTS.md)与[项目 Skill](.agents/skills/yorushika-visuals/SKILL.md)：换会话或模型时先读。
- [视频工作流](.agents/skills/yorushika-visuals/references/live-workflow.md)、[官方参考](.agents/skills/yorushika-visuals/references/visual-sources.md)、[更新记录](CHANGELOG.md)。

测试：`python3 -m unittest discover -s tests -v`，额外测试依赖见 `requirements-dev.txt`。

旧歌词卡版本保留在 [codex/legacy-lyric-card](https://github.com/Yorushikamimimi/Yorushika-Lyric-Card-Bot/tree/codex/legacy-lyric-card) 分支，已停止维护。当前使用本地视觉工房。

## 许可与素材边界

自有代码按 [MIT License](LICENSE) 授权。视频、音乐、歌词、官方人物与品牌、README中的视频画面、粉丝二创及第三方字体**不随代码获得MIT授权**，各自权利和使用限制见[第三方内容说明](THIRD_PARTY_NOTICES.md)。本项目不代表ヨルシカ官方，不分发本机原视频或完整图库。

`card_maker.py` 是保留的历史歌词卡模块，当前入口不使用它。旧 `.otf` 文件实际是404网页，不能作为字体；遗留模块会跳过无法加载的候选，使用可用的本机字体，或给出明确错误。字体仍需具备日文字形且允许用于你的用途；本项目不自动下载字体。
