# Changelog

All notable changes to this project will be documented in this file.

## [2026-10-03] README展示与贡献署名

- README精简为直观的功能、界面、启动与文档入口，加入用户提供的六张原始截图；其余素材仍仅保留本机。
- 移除两条旧提交的Claude共同作者署名，保留历史代码、作者和日期；当前main及旧版分支同步历史关系。原历史bundle保留在本机，散列映射和GitHub显示验证见 `CURRENT_PROGRESS.md`。

## [2026-10-03] 本地视觉工房主线

- 默认入口转为本机网页工房：浏览Live/MV候选与精选、加载真实原帧、按电脑/手机/头像规格裁切导出，并查看本地GPT二创与插画。
- 固定使用方式为本机网页配合当前GPT主控；GPT通过内置图像工具制作二创，网页整理需求和展示成品，不增加外置Agent服务。
- 默认运行依赖统一到 `requirements.txt`，仅声明FastAPI、Uvicorn、Pydantic与Pillow；移除默认Playwright/Chromium与SlowAPI需求。视频处理使用已有ffmpeg/ffprobe；测试依赖单列，旧安装清单保留兼容引用。
- 将现有网页、视频工具、测试、Skill与接手文档纳入Git。源视频和 `output/` 中的候选、成品、来源记录仍保留在本机，GitHub更新不等同于素材备份。
- 旧歌词卡停用，历史分支 `codex/legacy-lyric-card` 发布时指向 `8d39bce`（本轮署名调整后为 `68c3420`）；不再维护抓词接口或Docker部署。旧脚本保留作参考。
- 实际测试、合入与推送状态以 `CURRENT_PROGRESS.md` 为准。

## [Unreleased] 历史歌词卡版本（已停止维护）

### Security Fixes

- **Remove Edge channel dependency**: Removed `channel="msedge"` from Playwright launcher in all scripts (`crawler.py`, `fetch_song_list.py`, `test_lyric_fetch.py`). Now uses default Chromium, which works on Linux/Docker.
- **Docker hardening**: Added non-root user (`appuser`), `HEALTHCHECK` directive, and `curl` dependency for health probes.
- **Rate limiting**: Added `slowapi` middleware to `/card` endpoint — 10 calls/minute per IP.
- **Dependency pinning**: Added minimum version constraints to `requirements.txt` to prevent unexpected updates.
- **Config environment variables**: `PROXY_URL` and `UTANET_ARTIST_URL` are now configurable via environment variables instead of hardcoded in 4 files.

### UI Improvements

- **Polaroid layout**: Rebuilt card layout with 40px white border, 1080x1080 photo area, and 180px bottom info section.
- **Song info footer**: Bottom area now displays artist name ("ヨルシカ / Yorushika") and song title.
- **Shadow effect**: Proper Polaroid shadow using `GaussianBlur` filter instead of flat dark border.
- **Line height**: Increased line spacing to 1.5x font size for better readability.
- **Overlay adjustment**: Reduced overlay opacity from 100 to 70 to preserve background visibility.
- **Font compatibility**: Added Linux font paths (`/usr/share/fonts/...`) and bundled `NotoSerifJP-Regular.otf` for Docker support.
- **Lyric extraction**: `crawler.get_lyric_by_url()` now returns both lyric text and song title as a tuple.

### Bug Fixes

- Fixed shadow layer bug where text was being drawn on shadow instead of just the card base.
- Fixed `asset/demo.jpg` path typo in README (should be `assets/demo.jpg`).

## [v1.0.0] - 2026-02-09

### Initial MVP

- FastAPI endpoint `GET /card` generates lyric cards
- Playwright async crawler for Uta-Net lyrics
- Pillow-based image synthesis with dark overlay
- Basic retry mechanism with human-like delays
- Dockerfile for deployment
