# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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
