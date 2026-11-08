# -*- coding: utf-8 -*-
"""Huff 模型 + 敏感性分析（被 analysis.py 调用）

Huff 概率引力模型：
    去门店 j 的概率  P_ij = (A_j / d_ij^β) / Σ_k (A_k / d_ik^β)
    A_j = 吸引力（评分，或连锁标识）；d_ij = 距离（km）；β = 距离衰减参数

这里做三件事：
    1. 现有门店份额：每家店能分到多少"客流"
    2. 候选点评估：每个网格中心如果新开一家，能抢到该格多少需求
    3. 敏感性分析：β 和吸引力代理换几组，看 Top10 候选点是否稳定
"""

import numpy as np
import pandas as pd

EARTH = 111.0
CHAINS = ["星巴克", "瑞幸", "luckin", "Manner", "Cotti", "库迪", "Tims", "天好",
          "Peet", "皮爷", "Seesaw", "M Stand", "Nowwa", "挪瓦", "Costa", "太平洋",
          "McCafe", "麦咖啡", "KFC", "肯德基", "西西弗", "不眠海", "幸运咖", "蜜雪"]

# 需求代理的权重：地铁站 = 人流入口，写字楼 = 工作日客群，商场 = 周末客群
DEMAND_WEIGHT = {"地铁站": 3.0, "写字楼": 1.0, "酒店": 1.5, "景区": 2.0, "商场": 2.0}


def _km(lng, lat, lat0):
    kx = EARTH * np.cos(np.radians(lat0))
    return np.column_stack([(lng - lng.min()) * 0, lng * kx, lat * EARTH])[:, 1:]


def build_grid(df, bbox, cell_km=1.0, demand_cats=None):
    """把城市切成 cell_km 网格，返回每个格子的需求强度 D_i 和中心坐标。"""
    lat0 = (bbox["lat_min"] + bbox["lat_max"]) / 2
    kx, ky = EARTH * np.cos(np.radians(lat0)), EARTH
    dlng, dlat = cell_km / kx, cell_km / ky
    g = df.copy()
    g["gx"] = ((g["lng"] - bbox["lng_min"]) / dlng).astype(int)
    g["gy"] = ((g["lat"] - bbox["lat_min"]) / dlat).astype(int)
    pivot = g.groupby(["gx", "gy", "category"]).size().unstack("category", fill_value=0)
    demand = np.zeros(len(pivot))
    for cat, w in (DEMAND_WEIGHT if demand_cats is None else demand_cats).items():
        if cat in pivot.columns:
            demand += pivot[cat].values * w
    centers = pd.DataFrame({
        "gx": pivot.index.get_level_values(0),
        "gy": pivot.index.get_level_values(1),
        "demand": demand,
    })
    centers["lng"] = bbox["lng_min"] + (centers["gx"] + 0.5) * dlng
    centers["lat"] = bbox["lat_min"] + (centers["gy"] + 0.5) * dlat
    centers["dist_to_min_lng"] = (centers["lng"] - bbox["lng_min"]) * kx
    centers["dist_to_min_lat"] = (centers["lat"] - bbox["lat_min"]) * ky
    return centers[centers["demand"] > 0].reset_index(drop=True)


def _dist_matrix(a_lng, a_lat, b_lng, b_lat, lat0):
    kx, ky = EARTH * np.cos(np.radians(lat0)), EARTH
    dx = (a_lng[:, None] - b_lng[None, :]) * kx
    dy = (a_lat[:, None] - b_lat[None, :]) * ky
    return np.sqrt(dx ** 2 + dy ** 2)


def attractiveness(df, mode="rating"):
    """门店吸引力：rating 用评分（缺失取均值）；chain 用连锁=2/独立=1。"""
    if mode == "chain":
        is_chain = df["name"].astype(str).apply(lambda s: any(b in s for b in CHAINS))
        return np.where(is_chain, 2.0, 1.0)
    r = pd.to_numeric(df.get("rating"), errors="coerce")
    return r.fillna(r.mean() if r.notna().any() else 1.0).values


def huff_existing(coffee, demand, beta=1.5, mode="rating", lat0=22.6):
    """现有门店的市场份额：visits_j / 总需求。"""
    d = _dist_matrix(demand["lng"].values, demand["lat"].values,
                     coffee["lng"].values, coffee["lat"].values, lat0)
    d = np.maximum(d, 0.05)
    A = attractiveness(coffee, mode)
    w = A[None, :] / d ** beta
    p = w / w.sum(axis=1, keepdims=True)
    visits = demand["demand"].values @ p
    total = demand["demand"].sum()
    out = coffee[["name", "lng", "lat", "adname"]].copy()
    out["share"] = visits / total if total else 0
    return out.sort_values("share", ascending=False)


def huff_candidates(coffee, demand, beta=1.5, mode="rating", lat0=22.6, a_new=None):
    """每个格中心新开一家，能抢到该格多少需求（0–1）。"""
    d = _dist_matrix(demand["lng"].values, demand["lat"].values,
                     coffee["lng"].values, coffee["lat"].values, lat0)
    d = np.maximum(d, 0.05)
    A = attractiveness(coffee, mode)
    a_new = float(np.mean(A)) if a_new is None else a_new
    base = (A[None, :] / d ** beta).sum(axis=1)
    cand = demand.copy()
    cand["capture"] = a_new / (base + a_new)          # 距离=0 的极限情形，近似"开在该格中心"
    return cand.sort_values("capture", ascending=False)


def sensitivity(coffee, demand, betas=(1.2, 1.5, 2.0), modes=("rating", "chain"),
                top_n=10, lat0=22.6):
    """换 β 和吸引力代理，看 TopN 候选点稳不稳。"""
    runs, tops = [], {}
    for mode in modes:
        for beta in betas:
            top = huff_candidates(coffee, demand, beta=beta, mode=mode, lat0=lat0).head(top_n)
            key = f"{mode}-β{beta}"
            tops[key] = set(zip(top["gx"], top["gy"]))
            runs.append({"mode": mode, "beta": beta,
                         "top1_capture": float(top["capture"].iloc[0]) if len(top) else 0,
                         "mean_capture": float(top["capture"].mean()) if len(top) else 0})
    base = tops.get("rating-β1.5", set())
    overlap = [{"组合": k, f"与基准重合(Top{top_n})": f"{len(v & base)}/{top_n}"}
               for k, v in tops.items()]
    return pd.DataFrame(runs), pd.DataFrame(overlap), tops
