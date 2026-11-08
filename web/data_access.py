# -*- coding: utf-8 -*-
"""网页的取数层：每次请求都重新读 CSV，所以页面上的数据永远是"最新一次采集"。

不依赖 Flask，只用标准库 + pandas（可选）。没有数据时返回空，页面负责提示。
"""

import csv
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent      # 项目根目录
DATA = BASE / "data"
OUT = DATA / "web_out"

# 研究范围（和取数脚本一致，用于算面积）
BBOX = {
    "深圳": {"lng_min": 113.85, "lng_max": 114.35, "lat_min": 22.48, "lat_max": 22.80},
    "三亚": {"lng_min": 109.40, "lng_max": 109.72, "lat_min": 18.18, "lat_max": 18.36},
}

CHAIN_WORDS = ["星巴克", "瑞幸", "luckin", "Manner", "Cotti", "库迪", "Tims", "Peet",
               "Seesaw", "M Stand", "Nowwa", "挪瓦", "Costa", "太平洋", "McCafe",
               "麦咖啡", "KFC", "肯德基", "幸运咖"]


def cities():
    """data/ 下有哪些城市的数据；条数多的排前面（默认打开数据最多的那个）。"""
    if not DATA.exists():
        return []
    found = []
    for p in DATA.glob("*_stores.csv"):
        if p.stat().st_size > 100:
            with p.open(encoding="utf-8-sig") as f:
                n = max(0, sum(1 for _ in f) - 1)
            found.append((n, p.name.replace("_stores.csv", "")))
    return [c for _, c in sorted(found, reverse=True)]


def data_time(city):
    """数据文件最后修改时间 —— 页面上要显示的"数据时间"。"""
    p = DATA / f"{city}_stores.csv"
    if not p.exists():
        return "无数据"
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def load(city):
    p = DATA / f"{city}_stores.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        try:
            r["lng_f"] = float(r["lng"])
            r["lat_f"] = float(r["lat"])
        except (TypeError, ValueError):
            r["lng_f"] = r["lat_f"] = None
    return rows


def _area(city):
    b = BBOX.get(city)
    if not b:
        return None
    import math
    lat0 = (b["lat_min"] + b["lat_max"]) / 2
    return ((b["lng_max"] - b["lng_min"]) * 111 * math.cos(math.radians(lat0))
            * (b["lat_max"] - b["lat_min"]) * 111)


def summary(city):
    rows = load(city)
    if not rows:
        return {"city": city, "empty": True, "data_time": data_time(city)}

    by_cat = {}
    for r in rows:
        by_cat[r.get("category", "")] = by_cat.get(r.get("category", ""), 0) + 1

    coffee = [r for r in rows if r.get("category") == "咖啡"]
    rated = [r for r in coffee if (r.get("rating") or "").strip()]
    costs = []
    for r in coffee:
        try:
            costs.append(float(r["cost"]))
        except (TypeError, ValueError):
            pass
    costs.sort()
    median_cost = costs[len(costs) // 2] if costs else None
    chain = sum(1 for r in coffee
                if any(w.lower() in (r.get("name") or "").lower() for w in CHAIN_WORDS))
    area = _area(city)

    return {
        "city": city,
        "empty": False,
        "data_time": data_time(city),
        "total": len(rows),
        "coffee": len(coffee),
        "coffee_ratio": len(coffee) / len(rows) if rows else 0,
        "by_cat": dict(sorted(by_cat.items(), key=lambda x: -x[1])),
        "density": (len(coffee) / area) if area else None,
        "area": area,
        "rating_pct": (len(rated) / len(coffee)) if coffee else 0,
        "median_cost": median_cost,
        "chain_pct": (chain / len(coffee)) if coffee else 0,
    }


def search(city, kw="", page=1, size=20):
    rows = load(city)
    if kw:
        kw_l = kw.lower()
        rows = [r for r in rows
                if kw_l in (r.get("name") or "").lower()
                or kw_l in (r.get("adname") or "").lower()
                or kw_l in (r.get("business_area") or "").lower()]
    total = len(rows)
    start = max(0, (page - 1) * size)
    return rows[start:start + size], total


def density_png(city):
    """生成（或复用）密度图，返回路径。图和数据文件时间戳一致就复用。"""
    src = DATA / f"{city}_stores.csv"
    if not src.exists():
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"{city}_density.png"
    if png.exists() and png.stat().st_mtime >= src.stat().st_mtime:
        return png

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    coffee = [r for r in load(city) if r.get("category") == "咖啡" and r["lng_f"]]
    if not coffee:
        return None
    b = BBOX.get(city)

    fig, ax = plt.subplots(figsize=(7, 4.6), dpi=140)
    ax.scatter([r["lng_f"] for r in coffee], [r["lat_f"] for r in coffee],
               s=7, alpha=0.7, c="#c0392b")
    if b:
        ax.set_xlim(b["lng_min"], b["lng_max"])
        ax.set_ylim(b["lat_min"], b["lat_max"])
    ax.set_title(f"{city}：咖啡门店分布（{len(coffee)} 家）")
    ax.set_xlabel("经度")
    ax.set_ylabel("纬度")
    fig.tight_layout()
    fig.savefig(png)
    plt.close(fig)
    return png
