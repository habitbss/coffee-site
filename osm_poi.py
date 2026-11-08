# -*- coding: utf-8 -*-
"""不用 key 的兜底方案：OpenStreetMap Overpass API

原理：Overpass 是 OSM 的公开查询接口，按"地图范围"直接返回 POI，不需要任何 key。
这里查三种：咖啡厅(amenity=cafe)、奶茶/饮品店(shop=beverages / cuisine=bubble_tea)、便利店(shop=convenience)。

跑：python osm_poi.py
"""

import json
import time

import requests

ENDPOINT = "https://overpass-api.de/api/interpreter"

# 北大/国贸一带 1.5km 范围（南,西,北,东）
BOXES = {
    "北京·国贸": (39.8990, 116.4500, 39.9160, 116.4720),
    "上海·人民广场": (31.2250, 121.4650, 31.2400, 121.4850),
}

QUERY = """
[out:json][timeout:60];
(
  node["amenity"="cafe"]({b});
  way["amenity"="cafe"]({b});
  node["shop"="beverages"]({b});
  way["shop"="beverages"]({b});
  node["shop"="convenience"]({b});
  way["shop"="convenience"]({b});
);
out center tags;
"""


def fetch(bbox, retry=3):
    south, west, north, east = bbox
    q = QUERY.format(b=f"{south},{west},{north},{east}")
    for i in range(1, retry + 1):
        try:
            r = requests.post(ENDPOINT, data={"data": q}, timeout=90,
                              headers={"User-Agent": "course-project-demo/1.0"})
            if r.status_code == 200:
                return r.json()
            print(f"  HTTP {r.status_code}，重试 {i}/3")
        except requests.RequestException as exc:
            print(f"  {type(exc).__name__}，重试 {i}/3")
        time.sleep(3 * i)
    return None


for city, bbox in BOXES.items():
    print(f"\n=== {city} ===")
    data = fetch(bbox)
    if not data:
        print("  查询失败（Overpass 有时不稳定，稍后重试）")
        continue

    counts = {"咖啡": 0, "饮品/奶茶": 0, "便利店": 0}
    named = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("amenity") == "cafe":
            counts["咖啡"] += 1
        elif tags.get("shop") == "beverages" or tags.get("cuisine", "").find("bubble") >= 0:
            counts["饮品/奶茶"] += 1
        elif tags.get("shop") == "convenience":
            counts["便利店"] += 1
        if tags.get("name"):
            named.append(tags["name"])

    print(f"  咖啡 {counts['咖啡']} 家，饮品/奶茶 {counts['饮品/奶茶']} 家，便利店 {counts['便利店']} 家")
    print(f"  有名字的点位 {len(named)} 个，示例：{named[:6]}")
    if named:
        print(f"  去重后品牌示例：{sorted(set(named))[:10]}")
