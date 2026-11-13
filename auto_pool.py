"""主页「系统挑的」图池：按本人口味给五场 Live 整场镜头打分，选高分池并出 2560 宽图。

离线手动运行（不常驻、不改收藏和图库清单）：
    python3 auto_pool.py build            # 收标签→算特征→训练→打分→选池→补出大图
    python3 auto_pool.py build --dry-run  # 只打分选池，不出大图
    python3 auto_pool.py overview         # 写 output/auto-pool/overview.html 一览（本机打开）

标签：待挑「留」=1、「不要」=0；图库 Live 收藏=1、未收藏=0（弱）；主页按过「别再放」的=0。
「不要」只是训练里的反例＋同镜头降分，不拉黑。源视频只读。
输出全部在 output/auto-pool/（本机，不进 Git）：pool.json、frames/、thumbs/、features/。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
POOL = OUTPUT / "auto-pool"
SHOWS = [  # 扫描目录 → 场次名（和图库 source_label 一致）
    ("auto-expand-2021zense-v1", "2021 前世"),
    ("auto-expand-2022gekkou-v1", "2022 月光"),
    ("auto-expand-2024zense-v1", "2024 前世"),
    ("auto-expand-2024tsukineko-v1", "2024 月猫"),
    ("auto-expand-2025tousaku-v1", "2025 盗作"),
]
MODEL = "openai/clip-vit-large-patch14"
POOL_SIZE = 600       # 高分池张数；每天从里面抽约 80
MIN_QUALITY = .12     # 和 auto_expand.shortlist 同一条「明显不能用」线
NEAR = 4.0            # 离收藏 / 图库已有图不到 4 秒算同一镜头
TAIL = 120.0          # 每场最后 2 分钟多是片尾字幕，不进池
FRAME_WIDTH = 2560


def pick_id(source: str, seconds: float) -> str:
    return hashlib.sha256(f"auto-pool|{source}|{seconds:.3f}".encode()).hexdigest()[:24]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- 标签 ----------
def manifests():
    for m in sorted(OUTPUT.glob("*/manifest.json")):
        try:
            yield m, load(m)
        except (OSError, ValueError):
            continue


def labels() -> tuple[list[dict], dict[str, list[tuple[float, float]]]]:
    """Return training rows and, per source video, (seconds, weight) of existing pictures.

    weight: 1 = 收藏（整镜头跳过），0 = 图库已有或「不要」（降分）。"""
    fav = {(f["manifest"], f["candidate_id"]) for f in load(OUTPUT / "favorites.json").get("items", [])}
    decisions = {(d["manifest"], d["candidate_id"]): d for d in load(OUTPUT / "review-decisions.json").get("items", [])
                 if isinstance(d, dict)} if (OUTPUT / "review-decisions.json").exists() else {}
    rows, taken = [], {}
    for m, data in manifests():
        if data.get("source_kind", "live") != "live":
            continue
        scope = m.relative_to(OUTPUT).as_posix()
        videos = {v.get("id"): v for v in data.get("videos", []) if isinstance(v, dict)}
        queue = data.get("review_queue") is True
        for c in data.get("candidates", []):
            source = c.get("source") or videos.get(c.get("video_id"), {}).get("source")
            cid = str(c.get("id", c.get("timestamp")))
            if not isinstance(source, str):
                continue
            seconds, img, y = float(c["timestamp"]), m.parent / str(c.get("preview")), None
            if queue:
                d = decisions.get((scope, cid))
                if d and d.get("decision") in ("keep", "drop"):
                    moment = d.get("moment", c.get("system_moment"))
                    if isinstance(moment, int) and 0 <= moment < len(c.get("moments", [])):
                        img = m.parent / c["moments"][moment]["preview"]
                        seconds = float(d.get("seconds") or seconds)
                    y = int(d["decision"] == "keep")
            else:
                y = int((scope, cid) in fav)
            if y is None:
                continue
            if (scope, cid) in fav:
                y = 1
            taken.setdefault(source, []).append((seconds, 1.0 if y else 0.0))
            if img.is_file():
                rows.append({"img": str(img), "y": y})
    # 主页按过「别再放」的系统图：按 hidden.json 直接读缩略图作反例，不依赖它还在不在当前图池（11-14）
    for key in sorted(hidden_ids()):
        if (POOL / "thumbs" / f"{key}.jpg").is_file():
            rows.append({"img": str(POOL / "thumbs" / f"{key}.jpg"), "y": 0})
    return rows, taken


def hidden_ids() -> set[str]:
    data = load(POOL / "hidden.json") if (POOL / "hidden.json").exists() else {}
    return {str(h.get("id")) for h in data.get("items", []) if isinstance(h, dict)}


# ---------- 候选 ----------
def shot_candidates(scan_dir: Path, show: str, taken: dict) -> list[dict]:
    scan = load(scan_dir / "scan-candidates.json")
    source = scan["source"]["path"]
    near = taken.get(source, [])
    kept = []
    for r in scan["candidates"]:
        if r["similar_to_prior"] or r["quality"]["quality"] < MIN_QUALITY or r["seconds"] > scan["duration"] - TAIL:
            continue
        a, b = r["shot_start"], r["shot_end"]
        hits = [w for t, w in near if abs(r["seconds"] - t) < NEAR or a <= t <= b]
        if any(w == 1.0 for w in hits):
            continue  # 同镜头已有收藏
        kept.append({**r, "penalty": bool(hits)})
    shots: dict[tuple, list] = {}
    for r in kept:
        shots.setdefault((r["shot_start"], r["shot_end"]), []).append(r)
    out = []
    for (a, b), rs in shots.items():
        chosen = []
        for r in sorted(rs, key=lambda r: -r["quality"]["quality"]):
            if len(chosen) < max(1, math.ceil((b - a) / 8)) and all(abs(r["seconds"] - c["seconds"]) >= 3 for c in chosen):
                chosen.append(r)
        out += chosen
    return [{"id": pick_id(source, r["seconds"]), "source": source, "show": show, "seconds": round(r["seconds"], 3),
             "song": r.get("chapter"), "img": r["analysis_file"], "quality": r["quality"]["quality"],
             "penalty": r["penalty"]} for r in sorted(out, key=lambda r: r["seconds"])]


# ---------- 特征 ----------
class Embedder:
    def __init__(self):
        import torch
        from transformers import CLIPModel, CLIPProcessor
        self.torch = torch
        self.model = CLIPModel.from_pretrained(MODEL, use_safetensors=True).eval()
        self.proc = CLIPProcessor.from_pretrained(MODEL)
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model.to(self.dev)
        self.cache_file = POOL / "features" / "clip.npz"
        self.cache = {}
        if self.cache_file.exists():
            z = np.load(self.cache_file, allow_pickle=False)
            self.cache = dict(zip(z["keys"].tolist(), z["vecs"]))

    @staticmethod
    def key(path: str) -> str:
        st = os.stat(path)
        return f"{path}|{st.st_size}|{st.st_mtime_ns}"

    def crops(self, path):
        im = Image.open(path).convert("RGB")
        w, h = im.size
        s = max(w, h)
        sq = Image.new("RGB", (s, s))
        sq.paste(im, ((s - w) // 2, (s - h) // 2))
        return [sq, im]

    def __call__(self, paths: list[str]) -> np.ndarray:
        keys = [self.key(p) for p in paths]
        todo = [i for i, k in enumerate(keys) if k not in self.cache]
        t0 = time.monotonic()
        with self.torch.no_grad():
            for n in range(0, len(todo), 32):
                batch = todo[n:n + 32]
                ims = [c for i in batch for c in self.crops(paths[i])]
                x = self.proc(images=ims, return_tensors="pt")["pixel_values"].to(self.dev)
                e = self.model.get_image_features(pixel_values=x)
                e = e if self.torch.is_tensor(e) else e.pooler_output
                e = (e / e.norm(dim=-1, keepdim=True)).cpu().numpy().reshape(len(batch), -1)
                for i, v in zip(batch, e):
                    self.cache[keys[i]] = v.astype(np.float16)
                if n // 32 % 20 == 0:
                    print(f"features {n + len(batch)}/{len(todo)} {time.monotonic() - t0:.0f}s", flush=True)
        if todo:
            self.save()
        return np.stack([self.cache[k].astype(np.float32) for k in keys])

    def save(self):
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_name("clip.tmp.npz")
        np.savez(tmp, keys=np.array(list(self.cache)), vecs=np.stack(list(self.cache.values())))
        os.replace(tmp, self.cache_file)


# ---------- 影片段落（两侧黑边） ----------
def is_film(path: str) -> bool:
    im = np.asarray(Image.open(path).convert("L").resize((200, 92)), dtype=np.float32)
    col = im.max(axis=0)
    lit = np.nonzero(col > 24)[0]
    if not len(lit):
        return False
    left, right = lit[0], 199 - lit[-1]
    return bool(20 <= left <= 60 and 20 <= right <= 60) and bool(col[:max(1, left - 2)].max() < 6 and col[200 - max(1, right - 2):].max() < 6)


# ---------- 出图 ----------
def render_frames(items: list[dict]) -> None:
    sys.path.insert(0, str(ROOT))
    from live_wallpaper import color_plan, extract_frame, probe_video
    frames, thumbs, luts = POOL / "frames", POOL / "thumbs", OUTPUT / "preview-cache" / "luts"
    frames.mkdir(parents=True, exist_ok=True)
    thumbs.mkdir(parents=True, exist_ok=True)
    luts.mkdir(parents=True, exist_ok=True)
    plans = {}
    todo = [p for p in items if not (frames / f"{p['id']}.jpg").is_file()]
    t0 = time.monotonic()
    for n, p in enumerate(todo, 1):
        src = Path(p["source"])
        if src not in plans:
            plans[src] = color_plan(probe_video(src), luts, "auto", 1000, 65)[0]
        png = frames / f"{p['id']}.png"
        try:
            extract_frame(src, p["seconds"], png, plans[src])
            with Image.open(png) as im:
                im = im.convert("RGB")
                if im.width > FRAME_WIDTH:
                    im = im.resize((FRAME_WIDTH, round(im.height * FRAME_WIDTH / im.width)), Image.LANCZOS)
                im.save(frames / f"{p['id']}.tmp.jpg", "JPEG", quality=88, optimize=True, progressive=True)
                im.resize((640, round(im.height * 640 / im.width)), Image.LANCZOS).save(thumbs / f"{p['id']}.jpg", "JPEG", quality=82)
            os.replace(frames / f"{p['id']}.tmp.jpg", frames / f"{p['id']}.jpg")
        except Exception as exc:  # 单张失败不停整批
            print(f"frame failed {p['id']}: {type(exc).__name__}", flush=True)
        finally:
            png.unlink(missing_ok=True)
        if n % 25 == 0 or n == len(todo):
            print(f"frames {n}/{len(todo)} {time.monotonic() - t0:.0f}s", flush=True)


# ---------- 主流程 ----------
def build(dry_run: bool) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    POOL.mkdir(parents=True, exist_ok=True)
    rows, taken = labels()
    cands = []
    for folder, show in SHOWS:
        d = OUTPUT / folder
        if (d / "scan-candidates.json").exists():
            got = shot_candidates(d, show, taken)
            print(f"{show}: {len(got)} 个镜头候选", flush=True)
            cands += got
        else:
            print(f"{show}: 还没扫描，跳过", flush=True)
    emb = Embedder()
    X = emb([r["img"] for r in rows])
    y = np.array([r["y"] for r in rows])
    clf = make_pipeline(StandardScaler(), LogisticRegression(C=.01, max_iter=5000, class_weight="balanced")).fit(X, y)
    hidden = hidden_ids()
    cands = [c for c in cands if c["id"] not in hidden]  # 按过「别再放」的不占图池位置（11-14）
    C = emb([c["img"] for c in cands])
    score = clf.predict_proba(C)[:, 1]
    for c, s in zip(cands, score):
        c["score"] = round(float(s) * (.8 if c["penalty"] else 1.0), 4)
    cands.sort(key=lambda c: -c["score"])
    # 五场均衡：每场各取本场前 POOL_SIZE/5（候选不够时由别场高分补齐）
    pool, seen = [], set()
    for _, show in SHOWS:
        for c in [c for c in cands if c["show"] == show][:POOL_SIZE // len(SHOWS)]:
            pool.append(c); seen.add(c["id"])
    for c in cands:
        if len(pool) >= POOL_SIZE:
            break
        if c["id"] not in seen:
            pool.append(c); seen.add(c["id"])
    pool.sort(key=lambda c: -c["score"])
    for c in pool:
        c["film"] = is_film(c["img"])
    if not dry_run:
        render_frames(pool)
    items = [{k: c[k] for k in ("id", "source", "show", "seconds", "song", "score", "film")} for c in pool
             if dry_run or (POOL / "frames" / f"{c['id']}.jpg").is_file()]
    result = {"version": 1, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "model": MODEL,
              "train": {"rows": len(rows), "positive": int(y.sum())}, "candidates": len(cands),
              "shows": {show: sum(c["show"] == show for c in items) for _, show in SHOWS}, "items": items,
              "notice": "系统按口味打分挑的，未经本人逐张确认"}
    target = POOL / ("pool.dry-run.json" if dry_run else "pool.json")
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)
    print(json.dumps({k: result[k] for k in ("train", "candidates", "shows")}, ensure_ascii=False), flush=True)
    return result


def overview() -> Path:
    import html
    data = load(POOL / "pool.json")
    hidden = {h.get("id") for h in load(POOL / "hidden.json").get("items", [])} if (POOL / "hidden.json").exists() else set()
    items = data["items"]
    shows = [show for _, show in SHOWS if any(p["show"] == show for p in items)]
    cards = {show: "".join(
        f'<figure class="{"gone" if p["id"] in hidden else ""}"><a href="frames/{p["id"]}.jpg" target="_blank"><img loading="lazy" src="thumbs/{p["id"]}.jpg" alt=""></a>'
        f'<figcaption><b>{html.escape(str(p.get("song") or "—"))}</b><span>{p["score"]:.2f}{" · 影片段落" if p.get("film") else ""}</span></figcaption></figure>'
        for p in sorted((p for p in items if p["show"] == show), key=lambda p: -p["score"])) for show in shows}
    tabs = "".join(f'<button data-s="{i}"{" aria-pressed=true" if not i else ""}>{html.escape(show)} <i>{sum(p["show"] == show for p in items)}</i></button>' for i, show in enumerate(shows))
    grids = "".join(f'<section data-s="{i}"{"" if not i else " hidden"}>{cards[show]}</section>' for i, show in enumerate(shows))
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>主页图池一览</title>
<style>:root{{--bg:#0f1218;--panel:#171b24;--line:rgba(255,255,255,.08);--ink:#ece8de;--ink2:#a9a59c;--sun:#ffd76a}}*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,"PingFang SC",sans-serif}}.wrap{{max-width:1320px;margin:0 auto;padding:32px 24px 60px}}
h1{{margin:0;font-size:26px}}p.sub{{color:var(--ink2);margin:6px 0 22px;max-width:760px}}
nav{{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:10px;z-index:3;padding:8px;border-radius:999px;background:rgba(23,27,36,.8);backdrop-filter:blur(14px);border:1px solid var(--line);width:max-content;max-width:100%}}
nav button{{border:0;border-radius:999px;padding:7px 16px;background:none;color:var(--ink2);font:inherit;cursor:pointer;transition:background .2s,color .2s}}
nav button[aria-pressed=true]{{background:var(--ink);color:#111}}nav i{{font-style:normal;opacity:.6;margin-left:4px}}
section{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-top:20px;animation:in .4s ease}}@keyframes in{{from{{opacity:0;transform:translateY(6px)}}}}
figure{{margin:0;border-radius:12px;overflow:hidden;background:var(--panel);border:1px solid var(--line);transition:transform .25s}}figure:hover{{transform:translateY(-2px)}}
figure img{{display:block;width:100%;aspect-ratio:2.17/1;object-fit:cover;background:#000}}figcaption{{display:flex;justify-content:space-between;gap:8px;padding:7px 10px;font-size:12.5px}}
figcaption b{{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}figcaption span{{color:var(--ink2);white-space:nowrap}}figure.gone{{opacity:.25;filter:grayscale(1)}}
@media (max-width:600px){{.wrap{{padding:20px 16px}}nav{{border-radius:16px}}}}</style></head><body><div class="wrap">
<h1>主页图池一览</h1><p class="sub">系统按你的收藏和「留／不要」学口味，从五场 Live 整场 {data['candidates']} 个镜头里挑出的 {len(items)} 张（每场 {len(items)//max(1,len(shows))} 张，按分数排）。主页每天从里面换一批约 80 张，和收藏 2:1 穿插；同一首歌一天最多 3 张。点图看 2560 宽原图；灰掉的是你在主页按过「别再放」的。生成于 {html.escape(data['built_at'])}。</p>
<nav>{tabs}</nav>{grids}</div>
<script>document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav button').forEach(x=>x.setAttribute('aria-pressed',x===b));document.querySelectorAll('section').forEach(s=>s.hidden=s.dataset.s!==b.dataset.s);scrollTo({{top:0,behavior:'smooth'}})}})</script></body></html>"""
    target = POOL / "overview.html"
    target.write_text(page, encoding="utf-8")
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("overview")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.dry_run)
        overview()
    elif args.cmd == "overview":
        print(overview())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
