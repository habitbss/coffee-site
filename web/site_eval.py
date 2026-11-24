# -*- coding: utf-8 -*-
"""选址评估：给一个经纬度，判断"这里适不适合开咖啡店"，并给出理由。

只用已有的 stores.csv（咖啡 + 地铁站/写字楼/商场/酒店/景区），不依赖外部服务。

评价逻辑（三段）：
  1. 需求：周边 800m 内的写字楼/地铁/商场/酒店越多，潜在客群越大
  2. 距离：离最近同类店 150–800m 最理想（既有客流又不贴身打），太近或太远都扣分
  3. 竞争：周边 500m 内已有咖啡店越多，切入越难

打分（0~1）：
  score = 0.60×需求分位 + 0.25×距离合适度 − 0.15×竞争分位
  最终再与 Huff 份额按 7:3 融合（见 HUFF_WEIGHT）

"分位"只在**有人流的格子**里算（郊区和空地的需求是 0，混进来会让分位失去区分度）。
"""

import bisect
import csv
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

BBOX = {
    "深圳": {"lng_min": 113.85, "lng_max": 114.35, "lat_min": 22.48, "lat_max": 22.80},
    "三亚": {"lng_min": 109.40, "lng_max": 109.72, "lat_min": 18.18, "lat_max": 18.36},
}

DEMAND_WEIGHT = {"地铁站": 3.0, "写字楼": 1.0, "商场": 2.0, "酒店": 1.5, "景区": 2.0}
CHAIN_WORDS = ["星巴克", "瑞幸", "luckin", "Manner", "Cotti", "库迪", "Tims",
               "Peet", "Seesaw", "M Stand", "Nowwa", "挪瓦", "Costa", "太平洋",
               "McCafe", "麦咖啡", "KFC", "肯德基", "幸运咖"]

R_DEMAND = 800      # 米：需求统计半径
R_COMP = 500        # 米：竞争统计半径

_cache: dict[str, tuple[float, list[dict]]] = {}
_base_cache: dict[str, tuple[float, dict]] = {}


# ---------------------------------------------------------------- 数据
def load(city: str) -> list[dict]:
    """按文件修改时间缓存：CSV 一更新，下次请求自动重读（保持"实时"）。"""
    path = DATA / f"{city}_stores.csv"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    hit = _cache.get(city)
    if hit and hit[0] == mtime:
        return hit[1]

    rows = []
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    r["lng_f"] = float(r["lng"])
                    r["lat_f"] = float(r["lat"])
                except (TypeError, ValueError):
                    continue
                rows.append(r)
    _cache[city] = (mtime, rows)
    return rows


def clear_cache():
    _cache.clear()
    _base_cache.clear()
    _grids.clear()


def _dist_km(lng1, lat1, lng2, lat2) -> float:
    kx = 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    dx = (lng1 - lng2) * kx
    dy = (lat1 - lat2) * 111.0
    return math.hypot(dx, dy)


class Grid:
    """把点按 0.005° 分桶，查"附近 N 米"时只扫邻近几桶。

    不这么做的话：1 万多个点 × 1600 个网格 = 上千万次距离计算，点一次要等半分钟。
    """

    CELL = 0.005

    def __init__(self, rows):
        self.buckets: dict[tuple[int, int], list] = {}
        for r in rows:
            self.buckets.setdefault(self._key(r["lng_f"], r["lat_f"]), []).append(r)

    def _key(self, lng, lat):
        return (int(math.floor(lng / self.CELL)), int(math.floor(lat / self.CELL)))

    def near(self, lng, lat, radius_m):
        dlat = radius_m / 111000.0
        dlng = radius_m / (111000.0 * max(0.2, math.cos(math.radians(lat))))
        i0, i1 = self._key(lng - dlng, lat)[0], self._key(lng + dlng, lat)[0]
        j0, j1 = self._key(lng, lat - dlat)[1], self._key(lng, lat + dlat)[1]
        out = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                for r in self.buckets.get((i, j), ()):
                    if _dist_km(lng, lat, r["lng_f"], r["lat_f"]) * 1000 <= radius_m:
                        out.append(r)
        return out

    def nearest_km(self, lng, lat, max_ring: int = 8):
        """一圈圈往外找最近点，避免对全部点做 O(N) 距离计算。"""
        if not self.buckets:
            return 5.0
        ci, cj = self._key(lng, lat)
        for ring in range(0, max_ring + 1):
            best = None
            for i in range(ci - ring, ci + ring + 1):
                for j in range(cj - ring, cj + ring + 1):
                    if max(abs(i - ci), abs(j - cj)) != ring:
                        continue          # 只看这一圈的边框
                    for r in self.buckets.get((i, j), ()):
                        d = _dist_km(lng, lat, r["lng_f"], r["lat_f"])
                        if best is None or d < best:
                            best = d
            if best is not None:
                return best
        return 5.0


_grids: dict[str, tuple[float, dict]] = {}


def grids(city: str) -> dict:
    """三类索引：全部、咖啡、需求设施（按数据时间缓存）。"""
    path = DATA / f"{city}_stores.csv"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    hit = _grids.get(city)
    if hit and hit[0] == mtime:
        return hit[1]

    rows = load(city)
    coffee = [r for r in rows if r.get("category") == "咖啡"]
    demand = [r for r in rows if r.get("category") in DEMAND_WEIGHT]
    g = {"all": Grid(rows), "coffee": Grid(coffee), "demand": Grid(demand),
         "n_all": len(rows), "n_coffee": len(coffee), "n_demand": len(demand)}
    _grids[city] = (mtime, g)
    return g


def _percentile(values, v):
    """中位秩分位：大量相同值时不会被算成高分（0 需求不会变成 65 分位）。"""
    if not values:
        return 0.5
    less = sum(1 for x in values if x < v)
    equal = sum(1 for x in values if x == v)
    return (less + 0.5 * equal) / len(values)


def _level(score: float):
    if score >= 0.65:
        return "推荐", "#16a34a"
    if score >= 0.5:
        return "可以开", "#ca8a04"
    if score >= 0.35:
        return "谨慎", "#ea580c"
    return "不建议", "#dc2626"


# ---------------------------------------------------------------- 实时 Top 候选点
_cand_cache: dict[tuple, tuple[float, list]] = {}

# ---------------------------------------------------------------- Huff 模型
# P_ij = (A_j / d_ij^β) / Σ_k (A_k / d_ik^β)
# 候选点能抢到多少需求，用它来重排 Top 候选点。
HUFF_BETA = 1.8          # 距离衰减：越大 = 越不愿意走远
HUFF_R_KM = 3.0          # 只看 3km 内的需求点和竞品（截断，控制计算量）
A_NEW = 4.0              # 新店吸引力代理（4.0 ≈ 评分 4 分的店）
A_DEFAULT = 4.0          # 已有店没有评分时的默认吸引力
A_CHAIN_BONUS = 1.15     # 连锁品牌吸引力加成
HUFF_WEIGHT = 0.30       # Huff 在最终排序里的权重（0 = 关掉，退回原打分）
HUFF_SHORTLIST = 60      # 先用原打分筛 60 个，再按 Huff 重排（性能与质量的折中）

_huff_cache: dict[str, tuple[float, list]] = {}


def _attractiveness(row) -> float:
    """吸引力 A_j：优先用评分，没有评分用默认值；连锁品牌加成。"""
    a = A_DEFAULT
    txt = (row.get("rating") or "").strip()
    try:
        val = float(txt)
        if 1.0 <= val <= 5.0:
            a = val
    except ValueError:
        pass
    name = (row.get("name") or "").lower()
    if any(w.lower() in name for w in CHAIN_WORDS):
        a *= A_CHAIN_BONUS
    return a


def huff_baseline(city: str) -> list[dict]:
    """每个需求点：坐标、权重 D_i、3km 内现有竞品的 Σ A_k/d^β。

    这是公式的分母。预先算好并按数据时间缓存，后面每个候选点只算自己的分子。
    """
    path = DATA / f"{city}_stores.csv"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    hit = _huff_cache.get(city)
    if hit and hit[0] == mtime:
        return hit[1]

    rows = load(city)
    g = grids(city)
    pts = []
    for r in rows:
        cat = r.get("category")
        if cat not in DEMAND_WEIGHT:
            continue
        denom = 0.0
        for s in g["coffee"].near(r["lng_f"], r["lat_f"], HUFF_R_KM * 1000):
            d = max(_dist_km(r["lng_f"], r["lat_f"], s["lng_f"], s["lat_f"]), 0.05)
            denom += _attractiveness(s) / d ** HUFF_BETA
        pts.append({"lng": r["lng_f"], "lat": r["lat_f"],
                    "w": float(DEMAND_WEIGHT[cat]), "denom": denom})
    _huff_cache[city] = (mtime, pts)
    return pts


def huff_share(city: str, lng: float, lat: float,
               beta: float = HUFF_BETA, a_new: float = A_NEW) -> dict | None:
    """候选点开一家新店的 Huff 结果。

    share    = 能抢到"覆盖范围内需求"的百分比（0~1）
    expected = 期望抢到的需求单位（Σ D_i × P_i，绝对值，越大市场越大）
    """
    pts = huff_baseline(city)
    if not pts:
        return None
    cap = covered = 0.0
    for p in pts:
        d = max(_dist_km(lng, lat, p["lng"], p["lat"]), 0.05)
        if d > HUFF_R_KM:
            continue
        covered += p["w"]
        num = a_new / d ** beta
        cap += p["w"] * (num / (p["denom"] + num))
    if covered <= 0:
        return None
    # 同类店铺数量：用来给份额一个"参照值"（均值 ≈ 1/(N+1)）
    n_comp = len(grids(city)["coffee"].near(lng, lat, HUFF_R_KM * 1000))
    return {"share": cap / covered, "expected": cap, "covered": covered,
            "n_comp_3km": n_comp, "avg_share": 1.0 / (n_comp + 1),
            "beta": beta, "a_new": a_new}


def _dist_fit(km: float) -> float:
    """离最近同类店多远算合适（0~1）。

    原来用"空档分位"：越远越好。这会把候选点推向城市边缘的空白地——
    实际开店不是越偏越好：
      < 150m  正面对打，不推荐
      150-800m 最理想（既有客流聚集，又不至于贴身竞争）
      800m-2km 可接受，越远越可能是没需求的片区
      > 3km   基本没人流
    """
    if km < 0.15:
        return 0.20
    if km <= 0.8:
        return 0.70 + 0.30 * (km - 0.15) / 0.65
    if km <= 2.0:
        return 1.00 - 0.45 * (km - 0.8) / 1.2
    if km <= 3.0:
        return 0.55 - 0.35 * (km - 2.0) / 1.0
    return 0.15


def _pools(base: dict) -> tuple[list, list, list]:
    """分位数的参照集：只用"有人流"的格子。

    为什么：整个 bbox 里大部分格子需求是 0（郊区/海面/山地），
    拿它们当参照，会让"需求分位 0.97"这种值失去区分度，
    所有候选点的分数挤在 0.45 左右，『推荐』档位永远摸不到。
    """
    demands = base.get("demand") or [0]
    zeros = [i for i, d in enumerate(demands) if d > 0]
    if len(zeros) >= 20:
        sd = sorted(demands[i] for i in zeros)
        sc = sorted(base["comp"][i] for i in zeros) or sorted(base["comp"])
        sn = sorted(base["nearest"][i] for i in zeros) or sorted(base["nearest"])
        return sd, sc, sn
    return sorted(demands), sorted(base["comp"]), sorted(base["nearest"])


_stats_cache: dict[str, tuple[float, dict]] = {}


def candidate_stats(city: str) -> dict:
    """候选池的档位分布：全城筛出多少个点、其中各档几个。

    为什么需要它：Top10 本身就是"最好的 10 个"，所以里面几乎不会有"不建议"。
    真正说明稀缺性的是这个统计：例如"60 个候选里只有 3 个达到推荐线"。
    """
    top_candidates(city)                      # 触发计算（会填 _stats_cache）
    path = DATA / f"{city}_stores.csv"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    hit = _stats_cache.get(city)
    return hit[1] if hit and hit[0] == mtime else {"n": 0}


def top_candidates(city: str, n: int = 10, cell: float = 0.004,
                   min_gap_m: float = 800, min_demand_pct: float = 0.5,
                   min_score: float = 0.3,
                   huff_weight: float = HUFF_WEIGHT) -> list[dict]:
    """全城扫格子，挑出评分最高的 n 个候选点（候选点之间至少隔 min_gap_m）。

    两阶段：
      1. 原打分（需求分位 / 空档分位 / 竞争分位）扫全城，留前 HUFF_SHORTLIST 个；
      2. 对这 60 个算 Huff 份额，按「原分数 ×(1-w) + Huff 相对份额 ×w」重排。

    为什么不全城算 Huff：一个候选点要遍历 600+ 个需求点，全城几万个格子算不动。
    数据文件一变（mtime 变）就自动重算，所以列表是"实时"的。
    """
    path = DATA / f"{city}_stores.csv"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    key = (city, cell, n, min_gap_m, min_demand_pct, min_score,
           round(huff_weight, 2))
    hit = _cand_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]

    rows = load(city)
    bbox = BBOX.get(city)
    if not rows or not bbox:
        return []

    g = grids(city)
    base = city_baseline(city)
    sd, sc, sn = _pools(base)

    def pct(sorted_vals, v):
        if not sorted_vals:
            return 0.5
        lo = bisect.bisect_left(sorted_vals, v)
        hi = bisect.bisect_right(sorted_vals, v)
        return (lo + 0.5 * (hi - lo)) / len(sorted_vals)

    cands = []
    lng = bbox["lng_min"]
    while lng <= bbox["lng_max"]:
        lat = bbox["lat_min"]
        while lat <= bbox["lat_max"]:
            near_d = g["demand"].near(lng, lat, R_DEMAND)
            if near_d:                       # 没人流设施的格子直接跳过（省时间）
                d = sum(DEMAND_WEIGHT[r["category"]] for r in near_d)
                p_d = pct(sd, d)
                if p_d < min_demand_pct:     # 需求不够旺的格子不当候选
                    lat += cell
                    continue
                c = len(g["coffee"].near(lng, lat, R_COMP))
                near_km = g["coffee"].nearest_km(lng, lat)
                fit = _dist_fit(near_km)
                score = 0.60 * p_d + 0.25 * fit - 0.15 * pct(sc, c)
                if near_km > 3:
                    score = min(score, 0.34)
                elif near_km > 1.5:
                    score = min(score, 0.45)   # 太远：可能是没需求，不是空白好机会
                score = max(0.0, min(1.0, score))
                if score >= min_score:
                    cands.append({"lng": round(lng, 6), "lat": round(lat, 6),
                                  "score": round(score, 3), "demand": d,
                                  "comp": c, "nearest_m": round(near_km * 1000)})
            lat += cell
        lng += cell

    cands.sort(key=lambda x: -x["score"])

    # ---- 第二阶段：Huff 重排 ----
    if cands and huff_weight > 0:
        short = cands[:HUFF_SHORTLIST]
        for c in short:
            h = huff_share(city, c["lng"], c["lat"])
            c["huff_share"] = round(h["share"], 4) if h else None
            c["huff_expected"] = round(h["expected"], 2) if h else 0.0
            c["huff_avg"] = round(h["avg_share"], 4) if h else None
            c["comp_3km"] = h["n_comp_3km"] if h else None
            c["score_base"] = c["score"]
        mx = max((c["huff_expected"] for c in short), default=0.0) or 1.0
        for c in short:
            hn = c["huff_expected"] / mx
            c["huff_norm"] = round(hn, 3)
            c["score"] = round((1 - huff_weight) * c["score_base"]
                               + huff_weight * hn, 3)
        cands = short + cands[HUFF_SHORTLIST:]
        cands.sort(key=lambda x: -x["score"])

    # 候选池的档位分布（说明"推荐"有多稀缺）
    dist: dict[str, int] = {"推荐": 0, "可以开": 0, "谨慎": 0, "不建议": 0}
    for c in cands:
        lv, _ = _level(c["score"])
        dist[lv] = dist.get(lv, 0) + 1
    _stats_cache[city] = (mtime, {"n": len(cands), **dist})

    picked: list[dict] = []
    for c in cands:
        if all(_dist_km(c["lng"], c["lat"], p["lng"], p["lat"]) * 1000 >= min_gap_m
               for p in picked):
            level, colour = _level(c["score"])
            picked.append({**c, "level": level, "colour": colour, "rank": len(picked) + 1})
        if len(picked) >= n:
            break

    _cand_cache[key] = (mtime, picked)
    return picked


# ---------------------------------------------------------------- 全城基准（分位数）
def city_baseline(city: str, cell: float = 0.01) -> dict:
    """把城市划成网格，算每个格子的需求/竞争，作为分位参照（按数据时间缓存）。"""
    path = DATA / f"{city}_stores.csv"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    hit = _base_cache.get(city)
    if hit and hit[0] == mtime:
        return hit[1]

    rows = load(city)
    bbox = BBOX.get(city)
    if not rows or not bbox:
        return {"demand": [0], "comp": [0], "nearest": [1.0], "n": 0}

    g = grids(city)
    coffee_all = [r for r in rows if r.get("category") == "咖啡"]

    demands, comps, nears = [], [], []
    lng = bbox["lng_min"]
    while lng <= bbox["lng_max"]:
        lat = bbox["lat_min"]
        while lat <= bbox["lat_max"]:
            d = sum(DEMAND_WEIGHT[r["category"]] for r in g["demand"].near(lng, lat, R_DEMAND))
            c = len(g["coffee"].near(lng, lat, R_COMP))
            if coffee_all:
                nearest = min(_dist_km(lng, lat, x["lng_f"], x["lat_f"]) for x in coffee_all)
            else:
                nearest = 5.0
            demands.append(d)
            comps.append(c)
            nears.append(nearest)
            lat += cell
        lng += cell
    result = {"demand": demands, "comp": comps, "nearest": nears, "n": len(demands)}
    _base_cache[city] = (mtime, result)
    return result


# ---------------------------------------------------------------- 单点评估
def evaluate(city: str, lng: float, lat: float) -> dict:
    rows = load(city)
    bbox = BBOX.get(city)
    if not rows:
        return {"ok": False, "error": f"{city} 还没有数据，先跑一次采集。"}
    if bbox and not (bbox["lng_min"] <= lng <= bbox["lng_max"]
                     and bbox["lat_min"] <= lat <= bbox["lat_max"]):
        return {"ok": False, "error": "这个点不在研究范围内，请在城市区域内点击。"}

    g = grids(city)
    coffee = [r for r in rows if r.get("category") == "咖啡"]

    near_demand = g["demand"].near(lng, lat, R_DEMAND)
    near_comp = g["coffee"].near(lng, lat, R_COMP)

    by_cat: dict[str, int] = {}
    for r in near_demand:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    demand_score_raw = sum(DEMAND_WEIGHT[c] * n for c, n in by_cat.items())

    nearest_km = min((_dist_km(lng, lat, r["lng_f"], r["lat_f"]) for r in coffee),
                     default=5.0)
    comp_count = len(near_comp)
    comp_chain = sum(1 for r in near_comp
                     if any(w.lower() in (r.get("name") or "").lower() for w in CHAIN_WORDS))
    comp_rating = [float(r["rating"]) for r in near_comp
                   if (r.get("rating") or "").strip().replace(".", "").isdigit()]

    base = city_baseline(city)
    sd, sc, sn = _pools(base)
    p_demand = _percentile(sd, demand_score_raw)
    p_comp = _percentile(sc, comp_count)
    p_nearest = _percentile(sn, nearest_km)
    if demand_score_raw <= 0:
        p_demand = 0.0          # 一个人流设施都没有，不能靠"分位"糊过去

    score = 0.60 * p_demand + 0.25 * _dist_fit(nearest_km) - 0.15 * p_comp
    score_base = score

    # Huff：这个点开一家店，能抢到多少需求
    huff = huff_share(city, lng, lat)
    if huff:
        # 用"份额"做微调：份额越高说明需求越容易被新点吃到
        score = 0.75 * score + 0.25 * huff["share"]

    # 硬规则：没人流 → 直接低分；离同类太远 → 上限压到"谨慎"
    if demand_score_raw <= 0:
        score = 0.05
    elif nearest_km > 3:
        score = min(score, 0.34)
    score = max(0.0, min(1.0, score))

    if score >= 0.65:
        level, colour = "推荐", "#16a34a"
    elif score >= 0.5:
        level, colour = "可以开", "#ca8a04"
    elif score >= 0.35:
        level, colour = "谨慎", "#ea580c"
    else:
        level, colour = "不建议", "#dc2626"

    reasons = []
    if demand_score_raw > 0:
        desc = "、".join(f"{c} {n} 个" for c, n in
                        sorted(by_cat.items(), key=lambda x: -DEMAND_WEIGHT[x[0]]))
        reasons.append(f"周边 {R_DEMAND}m 有人流设施：{desc}（需求分 {demand_score_raw:.1f}，"
                       f"全城前 {100 - p_demand * 100:.0f}%）。")
    else:
        reasons.append(f"周边 {R_DEMAND}m 内没有任何地铁/写字楼/商场，几乎没有人流来源。")

    if comp_count == 0:
        reasons.append(f"周边 {R_COMP}m 没有同类咖啡店，属于空白区；"
                       f"最近一家在 {nearest_km * 1000:.0f} 米外。")
    else:
        reasons.append(f"周边 {R_COMP}m 已有 {comp_count} 家咖啡店"
                       f"（其中连锁 {comp_chain} 家），竞争强度全城前 {100 - p_comp * 100:.0f}%。")

    if nearest_km < 0.2:
        reasons.append("离最近的店不到 200 米：正面对打，除非有独特定位，否则不建议。")
    elif nearest_km > 2:
        reasons.append("最近同类店超过 2 公里：可能是需求不足的区域，先确认人流再决定。")
    else:
        reasons.append(f"离最近同类店 {nearest_km * 1000:.0f} 米，属于正常竞争距离。")

    if comp_rating:
        avg = sum(comp_rating) / len(comp_rating)
        reasons.append(f"周边竞品平均评分 {avg:.1f}，"
                       f"{'对手口碑较强，需要有差异化' if avg >= 4.5 else '对手口碑一般，有机会靠品质切入'}。")

    if huff:
        reasons.append(f"Huff 模型：以吸引力 {huff['a_new']:.1f}、距离衰减 β={huff['beta']:.1f} 计算，"
                       f"新店可覆盖 {huff['covered']:.0f} 单位需求，"
                       f"预计抢到其中 {huff['share'] * 100:.0f}%（绝对量 {huff['expected']:.1f}）。")

    return {
        "ok": True,
        "city": city,
        "lng": lng,
        "lat": lat,
        "score": round(score, 3),
        "score_base": round(score_base, 3),
        "huff": ({"share": round(huff["share"], 3),
                  "expected": round(huff["expected"], 2),
                  "covered": round(huff["covered"], 1),
                  "beta": huff["beta"], "a_new": huff["a_new"]} if huff else None),
        "level": level,
        "colour": colour,
        "demand": {
            "counts": by_cat,
            "raw": round(demand_score_raw, 1),
            "percentile": round(p_demand, 3),
        },
        "competition": {
            "count": comp_count,
            "chain": comp_chain,
            "nearest_m": round(nearest_km * 1000),
            "percentile": round(p_comp, 3),
            "avg_rating": round(sum(comp_rating) / len(comp_rating), 2) if comp_rating else None,
        },
        "reasons": reasons,
        "baseline_cells": base["n"],
    }
