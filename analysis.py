# -*- coding: utf-8 -*-
"""咖啡门店空间竞争分析：读 stores.csv，出结论（深圳 / 三亚 都适用）

用法：
    python analysis.py                 分析 data/ 下所有城市的 stores.csv
    python analysis.py 深圳            只分析一个城市

输入（抓数脚本产出）：
    data/深圳_stores.csv, data/三亚_stores.csv
    列：city,id,category,name,typecode,type,lng,lat,adname,business_area,rating,cost,address

输出：
    终端：五个结论（分布 / 聚集度 / 商圈 / 价格与连锁 / 供需缺口）
    data/{城市}_网格统计.csv    每个 1km 格的咖啡数、写字楼数、残差
    data/对比_城市.csv          两市指标对比
    data/图_{城市}_密度.png     咖啡密度热力图
    data/图_{城市}_缺口.png     供需残差图（红=过饱和，蓝=缺口）
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

# 研究范围（和取数脚本一致，用于算面积和画图边界）
BBOX = {
    "深圳": {"lng_min": 113.85, "lng_max": 114.35, "lat_min": 22.48, "lat_max": 22.80},
    "三亚": {"lng_min": 109.40, "lng_max": 109.72, "lat_min": 18.18, "lat_max": 18.36},
}

# 连锁品牌识别（名字里出现就算连锁；可按需补）
CHAINS = ["星巴克", "瑞幸", "luckin", "Manner", "Cotti", "库迪", "Tims", "天好",
          "Peet", "皮爷", "Seesaw", "M Stand", "Nowwa", "挪瓦", "Costa", "太平洋",
          "McCafe", "麦咖啡", "KFC", "肯德基", "西西弗", "不眠海", "幸运咖", "蜜雪"]

EARTH = 111.0          # 1 度纬度 ≈ 111 km


def load_city(city):
    path = DATA / f"{city}_stores.csv"
    if not path.exists():
        print(f"  跳过 {city}：找不到 {path.name}")
        return None
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["lng"] = pd.to_numeric(df["lng"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["cost"] = pd.to_numeric(df["cost"], errors="coerce")
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df = df.dropna(subset=["lng", "lat"])
    return df


def km_scale(lat):
    """把经纬度差换算成公里"""
    return EARTH, EARTH * np.cos(np.radians(lat))


def nearest_neighbor_stats(xy, area_km2):
    """最近邻平均距离 + 最近邻指数 NNI（<1 聚集，≈1 随机，>1 均匀）"""
    n = len(xy)
    if n < 3:
        return None
    d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
    np.fill_diagonal(d, np.inf)
    d_obs = d.min(axis=1).mean()
    d_exp = 0.5 / np.sqrt(n / area_km2)          # 随机分布下的期望距离
    se = 0.26136 * np.sqrt(area_km2) / n
    z = (d_obs - d_exp) / se if se else 0
    return {"n": n, "d_obs_km": d_obs, "d_exp_km": d_exp,
            "NNI": d_obs / d_exp, "z": z,
            "pattern": "聚集" if z < -1.96 else ("均匀" if z > 1.96 else "随机")}


def grid_counts(df, bbox, cell_km, categories):
    """把城市切成 cell_km 见方的格子，统计每个格子里各类 POI 数量。"""
    lat0 = (bbox["lat_min"] + bbox["lat_max"]) / 2
    kx, ky = km_scale(lat0)
    dlat = cell_km / ky
    dlng = cell_km / kx
    out = df.copy()
    out["gx"] = ((out["lng"] - bbox["lng_min"]) / dlng).astype(int)
    out["gy"] = ((out["lat"] - bbox["lat_min"]) / dlat).astype(int)
    pivot = (out.groupby(["gx", "gy", "category"]).size()
                .unstack("category", fill_value=0).reset_index())
    for cat in categories:
        if cat not in pivot.columns:
            pivot[cat] = 0
    pivot["cx"] = bbox["lng_min"] + (pivot["gx"] + 0.5) * dlng
    pivot["cy"] = bbox["lat_min"] + (pivot["gy"] + 0.5) * dlat
    return pivot


def clusters_from_grid(pivot, min_points=5):
    """简易商圈识别：把有咖啡的格子按 8 邻域连通，连成片的就是一个商圈。"""
    cells = {(r.gx, r.gy): r.咖啡 for r in pivot.itertuples() if r.咖啡 > 0}
    seen, groups = set(), []
    for cell in cells:
        if cell in seen:
            continue
        stack, group = [cell], []
        seen.add(cell)
        while stack:
            cx, cy = stack.pop()
            group.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (cx + dx, cy + dy)
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        total = sum(cells[c] for c in group)
        if total >= min_points:
            groups.append({"cells": len(group), "coffee": total,
                           "center": (np.mean([c[0] for c in group]),
                                      np.mean([c[1] for c in group]))})
    return sorted(groups, key=lambda g: -g["coffee"])


def supply_demand(pivot, target="咖啡"):
    """网格回归：咖啡数 ~ 其他 POI 数。残差为正=店比预期多（过饱和），为负=缺口。"""
    others = [c for c in pivot.columns if c not in (target, "gx", "gy", "cx", "cy")]
    others = [c for c in others if pivot[c].sum() > 0]
    if not others:
        return None, []
    X = np.column_stack([np.ones(len(pivot))] + [pivot[c].values for c in others])
    y = pivot[target].values.astype(float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = np.clip(X @ coef, 0, None)          # 预测值不能是负数
    ss_res = ((y - pred) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot else 0
    pivot = pivot.copy()
    pivot["pred"] = pred
    pivot["resid"] = y - pred
    info = {"r2": r2, "coef": {k: round(float(v), 2) for k, v in zip(["截距"] + others, coef)}}
    return info, pivot.sort_values("resid")


def analyze(city, make_plots=True):
    df = load_city(city)
    if df is None or df.empty:
        return None
    bbox = BBOX.get(city)
    if bbox is None:
        print(f"  {city}: 没有配置 BBOX，跳过")
        return None

    coffee = df[df["category"] == "咖啡"].copy()
    lat0 = (bbox["lat_min"] + bbox["lat_max"]) / 2
    area = ((bbox["lng_max"] - bbox["lng_min"]) * km_scale(lat0)[0] *
            (bbox["lat_max"] - bbox["lat_min"]) * km_scale(lat0)[1])

    print(f"\n{'=' * 66}\n【{city}】咖啡门店 {len(coffee)} 家 ｜ 总 POI {len(df)} 条 ｜ 研究范围 {area:.0f} km²")
    print(f"  密度：{len(coffee) / area:.2f} 家/km²")

    # 1. 分布
    print("\n① 分布")
    print("  按行政区：" + "，".join(
        f"{k} {v}" for k, v in coffee["adname"].value_counts().head(6).items()))
    print("  按商圈：" + "，".join(
        f"{k} {v}" for k, v in coffee["business_area"].replace("", np.nan)
        .dropna().value_counts().head(6).items()) if coffee["business_area"].notna().any() else "  按商圈：（无）")

    # 2. 聚集度
    print("\n② 聚集度（最近邻）")
    kx, ky = km_scale(lat0)
    xy = np.column_stack([(coffee["lng"] - bbox["lng_min"]) * kx,
                          (coffee["lat"] - bbox["lat_min"]) * ky])
    nn = nearest_neighbor_stats(xy, area)
    if nn:
        print(f"  平均最近邻距离 {nn['d_obs_km'] * 1000:.0f} 米，随机期望 {nn['d_exp_km'] * 1000:.0f} 米")
        print(f"  NNI = {nn['NNI']:.2f}（z={nn['z']:.1f}）→ {nn['pattern']}")

    # 3. 商圈
    print("\n③ 商圈（1km 网格连通聚类）")
    cats = [c for c in df["category"].unique() if c != "咖啡"]
    pivot = grid_counts(df, bbox, 1.0, ["咖啡"] + cats)
    groups = clusters_from_grid(pivot, min_points=5)
    print(f"  识别出 {len(groups)} 个商圈；Top3：" + "，".join(
        f"{g['coffee']} 家（{g['cells']} 格）" for g in groups[:3]))

    # 4. 价格与连锁
    print("\n④ 价格与连锁")
    if coffee["cost"].notna().any():
        c = coffee["cost"].dropna()
        print(f"  人均：中位 {c.median():.0f} 元，区间 {c.min():.0f}–{c.max():.0f} 元"
              f"（有效 {len(c)}/{len(coffee)}）")
        bands = pd.cut(c, [0, 25, 40, 1000], labels=["<25", "25–40", ">40"])
        print("  价格带：" + "，".join(f"{k} {v}" for k, v in bands.value_counts().sort_index().items()))
    else:
        print("  人均：无数据")
    is_chain = coffee["name"].astype(str).apply(lambda s: any(b in s for b in CHAINS))
    print(f"  连锁率：{is_chain.mean():.0%}（{is_chain.sum()}/{len(coffee)}）")

    # 5. 供需缺口
    print("\n⑤ 供需匹配（咖啡数 ~ 其他 POI 数）")
    info, ranked = supply_demand(pivot, "咖啡")
    if info:
        print(f"  回归 R² = {info['r2']:.2f}；系数：{info['coef']}")
        # 只看"有实际需求"的格子（预期 ≥ 1 家），避免把空地当成缺口
        meaningful = ranked[ranked["pred"] >= 1]
        if meaningful.empty:
            meaningful = ranked.head(5)
        low = meaningful.head(5)
        high = ranked.tail(5).iloc[::-1]
        print(f"  有需求的格子 {len(meaningful)} 个；其中缺口最大的：")
        for r in low.itertuples():
            print(f"    ({r.cx:.4f},{r.cy:.4f}) 实际 {int(r.咖啡)} 家，预期 {r.pred:.1f} 家")
        print("  过饱和（店比预期多）的格子：")
        for r in high.itertuples():
            print(f"    ({r.cx:.4f},{r.cy:.4f}) 实际 {int(r.咖啡)} 家，预期 {r.pred:.1f} 家")
        ranked.to_csv(DATA / f"{city}_网格统计.csv", index=False, encoding="utf-8-sig")
        print(f"  逐格明细 -> {city}_网格统计.csv")
    else:
        print("  没有其他品类数据，做不了供需对比（是不是只抓了咖啡？）")

    if make_plots:
        try_plots(city, df, coffee, bbox, pivot, ranked)

    return {"city": city, "coffee": len(coffee), "area": area,
            "density": len(coffee) / area,
            "NNI": nn["NNI"] if nn else None,
            "clusters": len(groups),
            "chain_rate": float(is_chain.mean()),
            "r2": info["r2"] if info else None}


def try_plots(city, df, coffee, bbox, pivot, ranked):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # 让中文能正常显示，负号不乱码
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as exc:
        print(f"  （画图跳过：{exc}）")
        return

    # 密度图
    fig, ax = plt.subplots(figsize=(7, 5), dpi=140)
    ax.scatter(coffee["lng"], coffee["lat"], s=6, alpha=0.6, c="#c0392b", label="咖啡门店")
    other = df[df["category"] != "咖啡"]
    if len(other):
        ax.scatter(other.sample(min(len(other), 3000))["lng"],
                   other.sample(min(len(other), 3000))["lat"],
                   s=3, alpha=0.25, c="#2980b9", label="其他 POI")
    ax.set_xlim(bbox["lng_min"], bbox["lng_max"])
    ax.set_ylim(bbox["lat_min"], bbox["lat_max"])
    ax.set_title(f"{city}：咖啡门店分布（{len(coffee)} 家）")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(DATA / f"图_{city}_密度.png")
    plt.close(fig)

    # 残差图
    if ranked is not None and "resid" in ranked.columns:
        fig, ax = plt.subplots(figsize=(7, 5), dpi=140)
        sc = ax.scatter(ranked["cx"], ranked["cy"], c=ranked["resid"],
                        cmap="RdBu_r", s=40, edgecolor="k", linewidth=0.3)
        ax.set_xlim(bbox["lng_min"], bbox["lng_max"])
        ax.set_ylim(bbox["lat_min"], bbox["lat_max"])
        ax.set_title(f"{city}：供需残差（蓝=缺口，红=过饱和）")
        fig.colorbar(sc, ax=ax, label="实际 − 预期门店数")
        fig.tight_layout()
        fig.savefig(DATA / f"图_{city}_缺口.png")
        plt.close(fig)


def main():
    cities = sys.argv[1:] or ["深圳", "三亚"]
    results = [r for r in (analyze(c) for c in cities) if r]
    if len(results) > 1:
        df = pd.DataFrame(results)
        df["density"] = df["density"].round(2)
        df["NNI"] = df["NNI"].round(2)
        df["chain_rate"] = (df["chain_rate"] * 100).round(0).astype(str) + "%"
        df["r2"] = df["r2"].round(2)
        print(f"\n{'=' * 66}\n【城市对比】")
        print(df.to_string(index=False))
        df.to_csv(DATA / "对比_城市.csv", index=False, encoding="utf-8-sig")
        print("\n对比表 -> 对比_城市.csv")


if __name__ == "__main__":
    main()
