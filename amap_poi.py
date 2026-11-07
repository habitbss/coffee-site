# -*- coding: utf-8 -*-
"""高德 POI 取数（多城市版）：咖啡门店 + 需求代理

用法：
    $env:AMAP_KEY="你的key"                       （PowerShell）
    set AMAP_KEY=你的key                          （cmd）

    python amap_poi.py --city 深圳 --pilot --cats 咖啡   试点
    python amap_poi.py --city 深圳                       全量（深圳核心六区）
    python amap_poi.py --all                             深圳 + 三亚 都跑
    python amap_poi.py --city 深圳 --from-cache          不请求接口，只用缓存重出 CSV

输出（按城市分开）：
    data/深圳_poi_raw.jsonl   原始记录，断点续跑靠它
    data/深圳_stores.csv      去重后的干净表

每个城市的需求代理不一样，这是刻意的：
    深圳（通勤城市）→ 地铁站 + 写字楼 + 商场
    三亚（旅游城市）→ 酒店 + 景区 + 商场
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

# ============================ 配置区 ============================
KEY = os.environ.get("AMAP_KEY", "")

CITIES = {
    "深圳": {
        # 核心六区：南山—福田—罗湖—盐田—宝安—龙华
        "bbox": {"lng_min": 113.85, "lng_max": 114.35, "lat_min": 22.48, "lat_max": 22.80},
        # 试点：福田 CBD
        "pilot": {"lng_min": 114.02, "lng_max": 114.08, "lat_min": 22.52, "lat_max": 22.56},
        "step": 0.04, "radius": 1900,
        "cats": ["咖啡", "地铁站", "写字楼", "商场"],
    },
    "三亚": {
        # 主城 + 三亚湾 / 大东海 / 亚龙湾 / 海棠湾
        "bbox": {"lng_min": 109.40, "lng_max": 109.72, "lat_min": 18.18, "lat_max": 18.36},
        # 试点：吉阳市区
        "pilot": {"lng_min": 109.49, "lng_max": 109.55, "lat_min": 18.22, "lat_max": 18.28},
        "step": 0.03, "radius": 1400,
        "cats": ["咖啡", "酒店", "景区", "商场"],
    },
}

# 品类定义：types 是高德类型编码（精确），keywords 只做二次校验
CATEGORIES = {
    "咖啡":   {"keywords": "咖啡厅",   "types": "050500"},
    "地铁站": {"keywords": "地铁站",   "types": ""},
    "写字楼": {"keywords": "写字楼",   "types": ""},
    "酒店":   {"keywords": "酒店",     "types": ""},
    "景区":   {"keywords": "景点",     "types": ""},
    "商场":   {"keywords": "购物中心", "types": ""},
}

PAGE_SIZE = 25     # 高德单页上限
MAX_PAGE = 20      # 每格每类最多翻 20 页
SLEEP = 0.35       # 请求间隔（秒），对应 QPS≈3

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
AROUND = "https://restapi.amap.com/v5/place/around"
ERR = {
    "10001": "key 无效", "10003": "今日配额用完", "10004": "请求过于频繁",
    "10009": "key 平台类型不对（要 Web服务）", "10012": "权限不足/未实名",
    "10021": "并发超限", "10026": "参数不合法", "20000": "参数错误",
}


# ============================ 工具 ============================
def grid_centers(bbox, step):
    cells, lat = [], bbox["lat_min"]
    while lat <= bbox["lat_max"]:
        lng = bbox["lng_min"]
        while lng <= bbox["lng_max"]:
            cells.append((round(lng, 5), round(lat, 5)))
            lng += step
        lat += step
    return cells


def load_done(path):
    done = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8-sig") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cell, cat = rec.get("_cell"), rec.get("_cat")
            if cell and cat:
                done.add((round(cell[0], 5), round(cell[1], 5), cat))
    return done


def around(cfg, radius, cell):
    """抓一个格子的一类 POI。失败抛 RuntimeError。"""
    lng, lat = cell
    pois, page = [], 1
    while page <= MAX_PAGE:
        params = {
            "key": KEY,
            "keywords": cfg["keywords"],
            "types": cfg["types"],
            "location": f"{lng},{lat}",
            "radius": str(radius),
            "page_size": str(PAGE_SIZE),
            "page_num": str(page),
            "show_fields": "business",     # 不加这句 rating/cost 不返回
        }
        try:
            data = requests.get(AROUND, params=params, timeout=20).json()
        except (requests.RequestException, ValueError) as exc:
            if page == 1:
                raise RuntimeError(f"{type(exc).__name__}: {exc}")
            break

        if str(data.get("status")) != "1":
            code = str(data.get("infocode"))
            raise RuntimeError(f"infocode={code} {ERR.get(code, data.get('info'))}")

        batch = data.get("pois", [])
        pois.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP)

    if len(pois) >= MAX_PAGE * PAGE_SIZE:
        print(f"  [注意] 格 {cell} 翻满 {MAX_PAGE} 页，可能漏数据 → 调小 step/radius")
    return pois


# ============================ 单城市流程 ============================
def run_city(city, pilot=False, only_cats="", from_cache=False):
    conf = CITIES[city]
    cats = {k: CATEGORIES[k] for k in conf["cats"]
            if (not only_cats or k in only_cats.split(","))}
    if not cats:
        print(f"  {city}: --cats 没匹配上，可选 {conf['cats']}")
        return None

    bbox = conf["pilot"] if pilot else conf["bbox"]
    cells = grid_centers(bbox, conf["step"])
    data_dir = DATA
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_path = data_dir / f"{city}_poi_raw.jsonl"
    csv_path = data_dir / f"{city}_stores.csv"

    print(f"\n=== {city} {'试点' if pilot else '全量'}：{len(cells)} 格 × {len(cats)} 类"
          f" ≈ {len(cells) * len(cats)} 次基础请求 | 品类 {list(cats)} ===")

    total_new = 0
    if not from_cache:
        done = load_done(raw_path)
        with raw_path.open("a", encoding="utf-8") as raw:
            for i, cell in enumerate(cells, 1):
                for cat, cfg in cats.items():
                    if (cell[0], cell[1], cat) in done:
                        continue
                    try:
                        pois = around(cfg, conf["radius"], cell)
                    except RuntimeError as exc:
                        print(f"  [跳过] {cell} {cat}: {exc}")
                        if "10003" in str(exc) or "10004" in str(exc):
                            print("  配额/限速 → 先停，已抓的不丢，明天继续")
                            return None
                        continue
                    for p in pois:
                        rec = dict(p)
                        rec["_cell"] = [cell[0], cell[1]]
                        rec["_cat"] = cat
                        raw.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        total_new += 1
                    time.sleep(SLEEP)
                raw.flush()
                if i % 20 == 0 or i == len(cells):
                    print(f"  进度 {i}/{len(cells)} 格，新写入 {total_new} 条")
        print(f"  抓取结束：新增 {total_new} 条")

    # ---------------- 解析成 CSV ----------------
    rows, cat_count, tc_by_cat = {}, {}, {}
    with raw_path.open(encoding="utf-8-sig") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            loc = rec.get("location", "")
            if "," not in loc:
                continue
            lng, lat = loc.split(",")
            cat, tc = rec.get("_cat", ""), rec.get("typecode", "")
            biz = rec.get("business") or {}
            cat_count[cat] = cat_count.get(cat, 0) + 1
            tc_by_cat.setdefault(cat, {})
            tc_by_cat[cat][tc] = tc_by_cat[cat].get(tc, 0) + 1
            rows[rec["id"]] = {
                "city": city,
                "id": rec["id"],
                "category": cat,
                "name": rec.get("name", ""),
                "typecode": tc,
                "type": rec.get("type", ""),
                "lng": lng,
                "lat": lat,
                "adname": rec.get("adname", ""),
                "business_area": rec.get("business_area") or "",
                "rating": biz.get("rating", ""),
                "cost": biz.get("cost", ""),
                "address": rec.get("address", ""),
            }

    if not rows:
        print(f"  {city}: 没有数据（去掉 --from-cache 先抓）")
        return None

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(next(iter(rows.values())).keys()))
        w.writeheader()
        w.writerows(rows.values())

    print(f"  去重后 {len(rows)} 条 -> {csv_path.name}")
    print("  按品类：" + "，".join(f"{c} {n}" for c, n in sorted(cat_count.items(), key=lambda x: -x[1])))
    for cat, codes in tc_by_cat.items():
        top = sorted(codes.items(), key=lambda x: -x[1])[:3]
        print(f"    {cat} typecode: " + ", ".join(f"{c}×{n}" for c, n in top))

    coffee = [r for r in rows.values() if r["category"] == "咖啡"]
    if coffee:
        rated = sum(1 for r in coffee if r["rating"])
        print(f"  咖啡 {len(coffee)} 家，有评分 {rated} 家（{rated / len(coffee):.0%}）")
    return {"city": city, "coffee": len(coffee), "total": len(rows),
            "rated_ratio": (rated / len(coffee)) if coffee else 0}


# ============================ 入口 ============================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="深圳", choices=list(CITIES))
    ap.add_argument("--all", action="store_true", help="跑配置里所有城市")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--cats", default="", help="只抓某几类，逗号分隔，如 咖啡")
    ap.add_argument("--from-cache", action="store_true", help="不请求接口，只用缓存出 CSV")
    args = ap.parse_args()

    if not args.from_cache and not KEY:
        print('没有 key：$env:AMAP_KEY="你的key"（PowerShell） 或 set AMAP_KEY=你的key（cmd）')
        return 1

    targets = list(CITIES) if args.all else [args.city]
    summary = [r for r in (run_city(c, args.pilot, args.cats, args.from_cache) for c in targets) if r]

    if len(summary) > 1:
        print("\n=== 城市对比 ===")
        for s in summary:
            print(f"  {s['city']}: 咖啡 {s['coffee']} 家 / 总计 {s['total']} 条，"
                  f"有评分 {s['rated_ratio']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
