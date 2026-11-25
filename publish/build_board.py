#!/usr/bin/env python3
"""Rebuild output/publish-20261118/board/board.json from authoritative sources.

Usage:
    python3 publish/build_board.py
    python3 publish/build_board.py --apply-updates /tmp/updates.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = BOT_ROOT / "output"
CATALOG_PATH = BOT_ROOT / "publish" / "board_catalog.json"
LEDGER_PATH = BOT_ROOT / "publish" / "ledger.csv"
MANIFEST_PATH = OUT_ROOT / "pixel-accepted" / "manifest.json"
PICKS_PATH = OUT_ROOT / "publish-20261118" / "picks.json"
REVIEW_PATH = OUT_ROOT / "publish-20261118" / "compare" / "review.json"
FAV_LINES_PATH = OUT_ROOT / "fav-lines-20261126" / "收藏新线适配.json"
DEFAULT_VAULT_CARDS = Path("/Users/yang/Vaults/Obsidian_repo/Vault/Personal_Archive/50-Workflow/外派卡")
DEFAULT_BOARD_OUT = OUT_ROOT / "publish-20261118" / "board" / "board.json"

VALID_STAGES = (
    "收藏待排",
    "制作中·待领",
    "待我初核",
    "待你终核",
    "返修中",
    "已通过",
)
VALID_DISPOSITIONS = (
    "待排发",
    "已入发帖夹",
    "已发布",
    "备选留存",
    "边角料",
    "已淘汰·搁置",
)
PLATFORMS = ("xhs", "dy", "x", "ig")
PLATFORM_MAP = {
    "小红书": "xhs",
    "抖音": "dy",
    "X": "x",
    "Instagram": "ig",
}
MANIFEST_CAT_MAP = {
    "yoru-zense2024": "前世 2024",
    "yoru-tousaku2025": "盗作 2025",
    "yoru-tsukineko2024": "月猫 2024",
    "yoru-gekkou2022": "月光 2022",
    "yoru-zense2021": "前世 2021",
    "yoru-ref": "参考图",
    "yoru-creative": "创作 · 四季小景",
    "yoru-anim": "动图 · 四季小景",
}
# Ready-for-publish folders already prepared in ~/Pictures/发布/ for legacy works
X_READY_BATCH4_WORKS = {
    "yoru-tousaku2025-y",
    "yoru-tousaku2025-t",
    "yoru-tousaku2025-u",
    "yoru-tousaku2025-v",
    "yoru-tousaku2025-w",
    "yoru-tousaku2025-x",
    "yoru-tousaku2025-z",
}


def load_vault_cards(vault_dir: Path) -> dict[str, dict]:
    """Parse Vault 外派卡/鹿*.md and optional .交回.md to determine card progress."""
    cards: dict[str, dict] = {}
    if not vault_dir.is_dir():
        return cards

    for path in sorted(vault_dir.glob("鹿*.md")):
        if path.name.endswith(".交回.md"):
            continue
        m = re.match(r"^(鹿\d+)", path.name)
        if not m:
            continue
        cid = m.group(1)
        text = path.read_text(encoding="utf-8", errors="ignore")
        fm: dict[str, str] = {}
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                for line in text[3:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        fm[k.strip()] = v.strip()

        rev_rounds = len(re.findall(r"^##\s+返修单·第", text, re.M))
        ret_path = vault_dir / f"{path.stem}.交回.md"
        max_ver = 0
        has_handback = False
        user_opinions = 0
        if ret_path.is_file():
            rtxt = ret_path.read_text(encoding="utf-8", errors="ignore")
            ver_nums = [int(x) for x in re.findall(r"^##\s+\d{4}-\d{2}-\d{2}｜第\s*(\d+)\s*版", rtxt, re.M)]
            max_ver = max(ver_nums) if ver_nums else 0
            has_handback = bool(ver_nums) or bool(re.search(r"^##\s+\d{4}-\d{2}-\d{2}", rtxt, re.M))
            user_opinions = len(re.findall(r"^###\s+本人验收意见", rtxt, re.M))

        ai_initial_checked = bool(
            re.search(r"反重力|Claude 桌面版 Code", text)
            and re.search(r"初核通过|挂验收台|已过|已同步\s*review\.json", text)
        )
        cards[cid] = {
            "id": cid,
            "file": path.name,
            "status": fm.get("status", "未知"),
            "updated": fm.get("updated", ""),
            "rev_rounds": rev_rounds,
            "max_ver": max_ver,
            "has_handback": has_handback,
            "user_opinions": user_opinions,
            "ai_initial_checked": ai_initial_checked,
        }
    return cards


def load_review_map(review_path: Path) -> dict[str, dict]:
    if not review_path.is_file():
        return {}
    data = json.loads(review_path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for card in data.get("cards", []):
        cid = str(card.get("id") or "")
        if cid:
            out[cid] = card
    return out


def derive_stage(
    card_id: str,
    vault_cards: dict[str, dict],
    review_map: dict[str, dict],
    stage_override: str | None = None,
) -> tuple[str, bool, bool, int, str]:
    """Map card metadata to Axis A (6-state progress stage).

    Returns (stage, ai_reviewed, user_reviewed, revision_round, vault_status).
    """
    if stage_override:
        if stage_override not in VALID_STAGES:
            raise ValueError(f"非法 stage_override: {stage_override}")
        if stage_override == "已通过":
            return "已通过", True, True, 0, "已通过"
        return stage_override, False, False, 0, stage_override

    if not card_id:
        return "收藏待排", False, False, 0, ""

    vc = vault_cards.get(card_id)
    rv = review_map.get(card_id)
    vstatus = vc["status"] if vc else (rv.get("status", "") if rv else "")
    rev_round = vc["rev_rounds"] if vc else 0

    if vstatus == "已通过":
        return "已通过", True, True, rev_round, vstatus
    if vstatus == "搁置":
        # User reviewed and decided to shelve
        return "已通过", True, True, rev_round, vstatus
    if vstatus == "退回":
        return "返修中", True, True, max(1, rev_round), vstatus
    if vstatus == "待验收":
        if rv and rv.get("status") == "待验收":
            return "待你终核", True, False, rev_round, vstatus
        if vc and (vc["ai_initial_checked"] or vc["has_handback"]):
            return "待你终核", True, False, rev_round, vstatus
        return "待我初核", False, False, rev_round, vstatus
    if vstatus in ("待领", "制作中"):
        if vc and vc["has_handback"] and not vc["ai_initial_checked"]:
            return "待我初核", False, False, rev_round, vstatus
        return "制作中·待领", False, False, rev_round, vstatus

    # Fallback: check review.json
    if rv:
        rs = rv.get("status", "")
        if rs in ("已通过", "已收口"):
            return "已通过", True, True, rev_round, rs
        if rs == "待验收":
            return "待你终核", True, False, rev_round, rs
    return "制作中·待领", False, False, rev_round, vstatus or "未知"


def load_ledger(ledger_path: Path) -> tuple[dict[str, dict[str, dict]], list[dict]]:
    """Index publish/ledger.csv by work_id -> {platform_key -> post_info}."""
    by_work: dict[str, dict[str, dict]] = {}
    rows: list[dict] = []
    if not ledger_path.is_file():
        return by_work, rows

    with ledger_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            plat = PLATFORM_MAP.get((row.get("platform") or "").strip())
            if not plat:
                continue
            url = (row.get("url") or "").strip()
            info = {
                "status": "published",
                "url": "" if url == "NA" else url,
                "date": (row.get("date") or "").strip(),
                "title": (row.get("title") or "").strip(),
                "views": (row.get("d7_views") or row.get("d3_views") or "").strip(),
                "likes": (row.get("d7_likes") or row.get("d3_likes") or "").strip(),
                "saves": (row.get("d7_saves") or row.get("d3_saves") or "").strip(),
                "comments": (row.get("d7_comments") or row.get("d3_comments") or "").strip(),
                "post_key": f"{plat}:{row.get('date','')}:{url or row.get('title','')}",
            }
            for w in (row.get("works") or "").split(";"):
                wid = w.strip()
                if not wid:
                    continue
                by_work.setdefault(wid, {})
                # Keep X latest if duplicated; preserve existing unless empty url
                if plat not in by_work[wid] or (not by_work[wid][plat]["url"] and info["url"]):
                    by_work[wid][plat] = info
    return by_work, rows


def build_platforms(
    work_key: str,
    ledger_by_work: dict[str, dict[str, dict]],
    override: dict[str, str] | None = None,
) -> dict[str, dict]:
    result = {p: {"status": "unpub", "url": "", "date": "", "title": "", "stats": ""} for p in PLATFORMS}
    matched = ledger_by_work.get(work_key, {}) if work_key else {}
    for p, info in matched.items():
        stats_parts = []
        if info["views"] and info["views"] != "NA":
            stats_parts.append(f"看 {info['views']}")
        if info["likes"] and info["likes"] != "NA":
            stats_parts.append(f"赞 {info['likes']}")
        if info["saves"] and info["saves"] != "NA":
            stats_parts.append(f"藏 {info['saves']}")
        result[p] = {
            "status": "published",
            "url": info["url"],
            "date": info["date"],
            "title": info["title"],
            "stats": " · ".join(stats_parts),
            "post_key": info["post_key"],
        }

    if override:
        for p, val in override.items():
            if p not in result:
                continue
            # Do not overwrite an already-published platform from ledger unless explicitly forced to 'skip'
            if result[p]["status"] == "published" and val == "ready":
                continue
            if val in ("unpub", "ready", "skip"):
                result[p]["status"] = val
    return result


def parse_picks(picks_path: Path) -> tuple[dict[str, dict], dict[str, str]]:
    if not picks_path.is_file():
        return {}, {}
    data = json.loads(picks_path.read_text(encoding="utf-8"))
    picked: dict[str, dict] = {}
    for b in data.get("batches", []):
        btitle = str(b.get("title") or "")
        for it in b.get("items", []):
            iid = str(it.get("id") or "")
            if iid:
                picked[iid] = {
                    "batch": btitle,
                    "status": str(it.get("status") or ""),
                    "fix_pix": str(it.get("fix_pix") or ""),
                    "ref": str(it.get("ref") or ""),
                }
    skipped: dict[str, str] = {}
    for s in data.get("skipped", []):
        if isinstance(s, dict) and s.get("id"):
            skipped[str(s["id"])] = str(s.get("reason") or "复看跳过不发")
    return picked, skipped


def import_manifest_items(
    manifest_path: Path,
    picks_path: Path,
    ledger_by_work: dict[str, dict[str, dict]],
) -> list[dict]:
    """Import all 141 items from pixel-accepted/manifest.json into legacy-live-pixel."""
    if not manifest_path.is_file():
        return []
    mf = json.loads(manifest_path.read_text(encoding="utf-8"))
    picked, skipped = parse_picks(picks_path)

    # IG batches already exported (posts 1..7)
    ig_folders = {
        "yoru-zense2024": "Instagram-4比5/第1-3帖",
        "yoru-tousaku2025": "Instagram-4比5/第4帖",
        "yoru-tsukineko2024": "Instagram-4比5/第5-6帖",
        "yoru-gekkou2022": "Instagram-4比5/第7帖",
        "yoru-zense2021": "Instagram-4比5/第7帖",
    }

    items: list[dict] = []
    for raw in mf.get("items", []):
        iid = str(raw["id"])
        cat = str(raw.get("category") or "")
        cat_label = MANIFEST_CAT_MAP.get(cat, cat)
        files = raw.get("files") or []
        media = ""
        # Prefer GIF first for yoru-anim, then scaled PNG, finally any file
        for f in files:
            p = str(f.get("path") or "")
            if f.get("format") == "gif" or p.endswith(".gif"):
                media = p
                break
        if not media:
            for f in files:
                if f.get("variant") == "scaled":
                    media = str(f.get("path") or "")
                    break
        if not media and files:
            media = str(files[0].get("path") or "")

        alt_media = [str(r) for r in (raw.get("ref_files") or []) if r]
        if iid in picked and picked[iid].get("fix_pix"):
            fix_p = picked[iid]["fix_pix"]
            if fix_p != media and fix_p not in alt_media:
                alt_media.insert(0, media)
                media = fix_p

        override: dict[str, str] = {}
        if iid in X_READY_BATCH4_WORKS:
            override["x"] = "ready"
        if iid in picked and cat in ig_folders and iid not in ledger_by_work.get(iid, {}):
            # Batch 1 already posted NA to IG; batches 2-7 are exported ready in Instagram-4比5/
            if "ig" not in ledger_by_work.get(iid, {}):
                override["ig"] = "ready"

        platforms = build_platforms(iid, ledger_by_work, override)
        has_published = any(v["status"] == "published" for v in platforms.values())

        if has_published:
            disp = "已发布"
            reason = ""
            note = picked.get(iid, {}).get("batch") or "已列入发帖记录"
        elif iid in skipped:
            disp = "已淘汰·搁置"
            reason = skipped[iid]
            note = "picks.json skipped 登记"
        elif iid in picked and picked[iid]["status"] == "只发Live":
            disp = "边角料"
            reason = "原帖只发 Live 截图，像素稿留存作边角料"
            note = picked[iid].get("batch", "")
        elif cat == "yoru-ref":
            disp = "备选留存"
            reason = ""
            note = "角色表情参考图（只读归档）"
        else:
            disp = "边角料"
            reason = "「原 Live＋像素」对照帖已收官，剩餘旧像素作边角料暂不单发（待有需要时再筛）"
            note = "未挑进已发批次的旧像素素材"

        ref_txt = str((raw.get("params") or {}).get("reference") or "")
        from_txt = str((raw.get("credit") or {}).get("from") or cat_label)
        items.append({
            "id": iid,
            "direction": "legacy-live-pixel",
            "batch": f"旧清单 · {cat_label}",
            "title": str(raw.get("name") or iid),
            "live": cat_label,
            "song": str(raw.get("usage") or from_txt),
            "tc": ref_txt[:28],
            "card": "已入清单",
            "planned_card": "",
            "stage": "已通过",
            "ai_reviewed": True,
            "user_reviewed": True,
            "revision_round": 0,
            "vault_status": "已通过",
            "versions": 2 if (iid in picked and picked[iid].get("fix_pix")) else 1,
            "disposition": disp,
            "disposition_reason": reason,
            "post_folder": "X-盗作2025/第4帖" if iid in X_READY_BATCH4_WORKS else "",
            "platforms": platforms,
            "media": media,
            "alt_media": alt_media,
            "note": note,
            "source": "manifest",
        })
    return items


def import_fav_items(
    fav_path: Path,
    fav_sheet_map: dict,
    vault_cards: dict[str, dict],
    review_map: dict[str, dict],
    ledger_by_work: dict[str, dict[str, dict]],
) -> list[dict]:
    """Import 64 curated favorite-pool candidates into the 5 active directions."""
    if not fav_path.is_file():
        return []
    data = json.loads(fav_path.read_text(encoding="utf-8"))
    items: list[dict] = []
    for sheet in data.get("sheets", []):
        sid = str(sheet.get("id") or "")
        smap = fav_sheet_map.get(sid)
        if not smap:
            raise ValueError(f"收藏新线适配.json 包含未映射的 sheet: {sid}")
        direction = smap["direction"]
        batch = smap["batch"]
        code_to_card = smap.get("code_to_card", {})
        for it in sheet.get("items", []):
            code = str(it["code"])
            raw_path = str(it["path"])
            media = raw_path[len("output/"):] if raw_path.startswith("output/") else raw_path
            planned_card = code_to_card.get(code, "")
            # If the planned card has already been issued in Vault, read live stage automatically!
            if planned_card and planned_card in vault_cards:
                stage, ai_ok, user_ok, rev_r, vstatus = derive_stage(planned_card, vault_cards, review_map)
                card_disp = planned_card
            else:
                stage, ai_ok, user_ok, rev_r, vstatus = "收藏待排", False, False, 0, ""
                card_disp = ""
            items.append({
                "id": f"fav-{code.lower()}",
                "direction": direction,
                "batch": batch,
                "title": f"{code} · {it.get('song', '')}",
                "live": str(it.get("live") or ""),
                "song": str(it.get("song") or ""),
                "tc": str(it.get("tc") or ""),
                "card": card_disp,
                "planned_card": planned_card,
                "stage": stage,
                "ai_reviewed": ai_ok,
                "user_reviewed": user_ok,
                "revision_round": rev_r,
                "vault_status": vstatus,
                "versions": 0 if not card_disp else 1,
                "disposition": "待排发",
                "disposition_reason": "",
                "post_folder": "",
                "platforms": build_platforms("", ledger_by_work),
                "media": media,
                "alt_media": [],
                "note": str(it.get("note") or ""),
                "source": "fav-lines",
                "code": code,
            })
    return items


def check_safe_media(rel: str, label: str) -> None:
    if not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        raise ValueError(f"[{label}] 非法相对路径: {rel!r}")
    p = (OUT_ROOT / rel).resolve()
    if not p.is_relative_to(OUT_ROOT.resolve()):
        raise ValueError(f"[{label}] 路径越出 output/: {rel!r}")
    if not p.is_file():
        raise FileNotFoundError(f"[{label}] 媒体文件不存在: {p}")


def build_board(
    catalog_path: Path = CATALOG_PATH,
    vault_dir: Path = DEFAULT_VAULT_CARDS,
    out_path: Path = DEFAULT_BOARD_OUT,
) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    vault_cards = load_vault_cards(vault_dir)
    review_map = load_review_map(REVIEW_PATH)
    ledger_by_work, ledger_rows = load_ledger(LEDGER_PATH)

    groups = catalog.get("groups", [])
    directions_raw = catalog.get("directions", [])
    dir_ids = {d["id"] for d in directions_raw}
    for d in directions_raw:
        check_safe_media(d["cover"], f"direction cover {d['id']}")

    all_items: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Explicit curated/finished items from board_catalog.json
    for raw in catalog.get("items", []):
        iid = str(raw["id"])
        if iid in seen_ids:
            raise ValueError(f"重复 item id: {iid}")
        seen_ids.add(iid)
        direction = str(raw["direction"])
        if direction not in dir_ids:
            raise ValueError(f"[{iid}] 未知 direction: {direction}")

        card_id = str(raw.get("card") or "")
        planned_card = str(raw.get("planned_card") or "")
        # If an unissued planned item now exists in Vault, switch to Vault status
        effective_card = card_id or (planned_card if planned_card in vault_cards else "")
        stage_ov = raw.get("stage_override")
        if effective_card and effective_card in vault_cards:
            stage_ov = None
        stage, ai_ok, user_ok, rev_r, vstatus = derive_stage(effective_card, vault_cards, review_map, stage_ov)

        ledger_key = str(raw.get("ledger_match") or "")
        platforms = build_platforms(ledger_key, ledger_by_work, raw.get("platforms_override"))
        versions = int(raw.get("versions") or 1)
        if effective_card in vault_cards and vault_cards[effective_card]["max_ver"] > versions:
            versions = vault_cards[effective_card]["max_ver"]

        all_items.append({
            "id": iid,
            "direction": direction,
            "batch": str(raw.get("batch") or ""),
            "title": str(raw.get("title") or iid),
            "live": str(raw.get("live") or ""),
            "song": str(raw.get("song") or ""),
            "tc": str(raw.get("tc") or ""),
            "card": effective_card,
            "planned_card": planned_card,
            "stage": stage,
            "ai_reviewed": ai_ok,
            "user_reviewed": user_ok,
            "revision_round": rev_r,
            "vault_status": vstatus,
            "versions": versions,
            "disposition": str(raw["disposition"]),
            "disposition_reason": str(raw.get("disposition_reason") or ""),
            "post_folder": str(raw.get("post_folder") or ""),
            "platforms": platforms,
            "media": str(raw["media"]),
            "alt_media": list(raw.get("alt_media") or []),
            "note": str(raw.get("note") or ""),
            "source": "catalog",
        })

    # 2. Manifest 141 legacy items
    for it in import_manifest_items(MANIFEST_PATH, PICKS_PATH, ledger_by_work):
        if it["id"] in seen_ids:
            raise ValueError(f"manifest item id 冲突: {it['id']}")
        seen_ids.add(it["id"])
        all_items.append(it)

    # 3. 64 fav-lines candidates
    for it in import_fav_items(FAV_LINES_PATH, catalog.get("fav_sheet_map", {}), vault_cards, review_map, ledger_by_work):
        if it["id"] in seen_ids:
            raise ValueError(f"fav item id 冲突: {it['id']}")
        seen_ids.add(it["id"])
        all_items.append(it)

    # 4. Apply manual disposition/note overrides from board_catalog.json
    overrides = catalog.get("overrides", {})
    for it in all_items:
        ov = overrides.get(it["id"])
        if not isinstance(ov, dict):
            continue
        if "disposition" in ov and ov["disposition"]:
            it["disposition"] = str(ov["disposition"])
        if "disposition_reason" in ov:
            it["disposition_reason"] = str(ov["disposition_reason"] or "")
        if "note" in ov:
            it["note"] = str(ov["note"] or "")
        if "post_folder" in ov:
            it["post_folder"] = str(ov["post_folder"] or "")
        if "platforms_override" in ov and isinstance(ov["platforms_override"], dict):
            for pk, pv in ov["platforms_override"].items():
                if pk in it["platforms"] and pv in ("unpub", "ready", "skip"):
                    it["platforms"][pk]["status"] = pv

    # 5. Validate all items strictly
    for it in all_items:
        if it["stage"] not in VALID_STAGES:
            raise ValueError(f"[{it['id']}] 非法 stage: {it['stage']}")
        if it["disposition"] not in VALID_DISPOSITIONS:
            raise ValueError(f"[{it['id']}] 非法 disposition: {it['disposition']}")
        if it["disposition"] == "已淘汰·搁置" and not it["disposition_reason"].strip():
            raise ValueError(f"[{it['id']}] disposition=已淘汰·搁置 必须填写 disposition_reason")
        check_safe_media(it["media"], f"item {it['id']}")
        for idx, am in enumerate(it["alt_media"]):
            check_safe_media(am, f"item {it['id']} alt_media[{idx}]")

    # 6. Compute per-direction stats (including unique published post count)
    directions_out: list[dict] = []
    totals = {
        "ready_to_publish": 0,
        "in_progress": 0,
        "published_posts": 0,
        "queued_fav": 0,
        "reserve": 0,
        "leftover": 0,
        "dropped": 0,
        "total_items": 0,
    }
    all_pub_post_keys: set[str] = set()
    for d in directions_raw:
        did = d["id"]
        ditems = [it for it in all_items if it["direction"] == did]
        pub_keys = {
            p["post_key"]
            for it in ditems
            for p in it["platforms"].values()
            if p.get("status") == "published" and p.get("post_key")
        }
        all_pub_post_keys |= pub_keys

        counts = {
            "ready_to_publish": sum(
                1 for it in ditems
                if it["stage"] == "已通过" and it["disposition"] in ("待排发", "已入发帖夹")
            ),
            "in_progress": sum(
                1 for it in ditems
                if it["stage"] in ("制作中·待领", "待我初核", "待你终核", "返修中")
            ),
            "published_posts": len(pub_keys),
            "queued_fav": sum(1 for it in ditems if it["stage"] == "收藏待排"),
            "approved_total": sum(1 for it in ditems if it["stage"] == "已通过"),
            "published_items": sum(1 for it in ditems if it["disposition"] == "已发布"),
            "reserve": sum(1 for it in ditems if it["disposition"] == "备选留存"),
            "leftover": sum(1 for it in ditems if it["disposition"] == "边角料"),
            "dropped": sum(1 for it in ditems if it["disposition"] == "已淘汰·搁置"),
            "total_items": len(ditems),
        }
        if d.get("group") != "drawer":
            for k in ("ready_to_publish", "in_progress", "queued_fav", "reserve", "leftover", "dropped", "total_items"):
                totals[k] += counts[k]

        dout = dict(d)
        dout["counts"] = counts
        directions_out.append(dout)

    totals["published_posts"] = len(all_pub_post_keys)

    # 7. Enrich planned_cards (鹿102–鹿118)
    planned_out: list[dict] = []
    for pc in catalog.get("planned_cards", []):
        pid = pc["id"]
        linked = [it for it in all_items if it.get("planned_card") == pid or it.get("card") == pid]
        vc = vault_cards.get(pid)
        planned_out.append({
            **pc,
            "vault_status": vc["status"] if vc else "未出卡",
            "linked_items": [it["id"] for it in linked],
            "thumbs": [it["media"] for it in linked[:6]],
            "actual_linked": len(linked),
        })

    board = {
        "schemaVersion": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {
            "catalog": "publish/board_catalog.json",
            "vault_cards_found": len(vault_cards),
            "ledger_rows": len(ledger_rows),
            "manifest_items": sum(1 for it in all_items if it["source"] == "manifest"),
            "fav_candidates": sum(1 for it in all_items if it["source"] == "fav-lines"),
        },
        "enums": {
            "stages": list(VALID_STAGES),
            "dispositions": list(VALID_DISPOSITIONS),
            "platforms": [
                {"id": "xhs", "short": "红", "name": "小红书"},
                {"id": "dy", "short": "抖", "name": "抖音"},
                {"id": "x", "short": "X", "name": "X / Twitter"},
                {"id": "ig", "short": "IG", "name": "Instagram"},
            ],
        },
        "totals": totals,
        "groups": groups,
        "directions": directions_out,
        "planned_cards": planned_out,
        "items": all_items,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out_path)
    return board


def apply_updates(updates_file: Path, catalog_path: Path = CATALOG_PATH) -> int:
    """Apply batch disposition/note overrides to board_catalog.json cleanly."""
    payload = json.loads(updates_file.read_text(encoding="utf-8"))
    updates = payload.get("updates") if isinstance(payload, dict) else payload
    if not isinstance(updates, list):
        raise ValueError("updates JSON 必须是列表或包含 updates 列表的对象")

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    overrides = catalog.setdefault("overrides", {})
    applied = 0
    for u in updates:
        iid = str(u.get("id") or "").strip()
        if not iid:
            raise ValueError("update 条目缺少 id")
        disp = u.get("disposition")
        reason = str(u.get("disposition_reason") or "").strip()
        if disp is not None:
            if disp not in VALID_DISPOSITIONS:
                raise ValueError(f"[{iid}] 非法 disposition: {disp}")
            if disp == "已淘汰·搁置" and not reason:
                raise ValueError(f"[{iid}] 已淘汰·搁置 必须附 disposition_reason")
        entry = overrides.setdefault(iid, {})
        for k in ("disposition", "disposition_reason", "note", "post_folder", "platforms_override"):
            if k in u:
                entry[k] = u[k]
        applied += 1

    tmp = catalog_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, catalog_path)
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description="Build yoru pixel creation board.json")
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--vault-cards", type=Path, default=Path(os.environ.get("YORU_VAULT_CARDS", DEFAULT_VAULT_CARDS)))
    parser.add_argument("--out", type=Path, default=DEFAULT_BOARD_OUT)
    parser.add_argument("--apply-updates", type=Path, help="Optional updates JSON file to merge into catalog overrides before rebuilding")
    args = parser.parse_args()

    if args.apply_updates:
        n = apply_updates(args.apply_updates, args.catalog)
        print(f"Applied {n} overrides into {args.catalog}")

    board = build_board(args.catalog, args.vault_cards, args.out)
    t = board["totals"]
    print(
        f"OK -> {args.out}\n"
        f"  directions={len(board['directions'])} planned_cards={len(board['planned_cards'])} total_items={len(board['items'])}\n"
        f"  totals(excl.drawer): 可发={t['ready_to_publish']} 在制={t['in_progress']} "
        f"已发帖={t['published_posts']} 收藏待排={t['queued_fav']} "
        f"备选={t['reserve']} 边角料={t['leftover']} 淘汰搁置={t['dropped']}"
    )
    for d in board["directions"]:
        c = d["counts"]
        print(
            f"  [{d['group']:7s}] {d['id']:20s} | 可发={c['ready_to_publish']:2d} 在制={c['in_progress']:2d} "
            f"已发帖={c['published_posts']:2d} 收藏={c['queued_fav']:2d} 备选={c['reserve']:2d} "
            f"边角={c['leftover']:2d} 淘汰={c['dropped']:2d} | {d['name']}"
        )


if __name__ == "__main__":
    main()
