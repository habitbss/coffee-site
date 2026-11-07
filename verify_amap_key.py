# -*- coding: utf-8 -*-
"""拿到 key 后先跑这个：5 分钟判断"这条路到底通不通"。

用法：
    set AMAP_KEY=你的key            （Windows cmd）
    $env:AMAP_KEY="你的key"         （PowerShell）
    python verify_amap_key.py

也可以直接：python verify_amap_key.py 你的key

它只做 5 件事，把"能不能当主数据源"问清楚：
  1. key 是否有效、平台类型对不对
  2. 关键词搜索能不能返回数据
  3. 周边搜索（网格法要用的那个接口）能不能用
  4. 单次搜索的返回上限大概多少（决定要不要切网格）
  5. 实际返回了哪些字段（rating / business_area / typecode 有没有）
"""

import json
import os
import sys

import requests

KEY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AMAP_KEY", "")
if not KEY:
    print("没有 key。用法：python verify_amap_key.py 你的key")
    sys.exit(1)

TEXT = "https://restapi.amap.com/v5/place/text"
AROUND = "https://restapi.amap.com/v5/place/around"
# 常见错误码（部分）
ERR = {
    "10001": "key 无效（没生效/复制错了）",
    "10003": "今日配额已用完",
    "10004": "访问过于频繁（QPS 超限）",
    "10009": "key 平台类型不对（多半是选成了 Web端JS API，要选『Web服务』）",
    "10012": "权限不足（可能未实名/未开通该服务）",
    "10013": "服务不可用",
    "10021": "并发超限",
    "10026": "请求参数不合法（常见 old/new 接口混用）",
    "10029": "海外日配额超限",
    "20000": "参数错误",
}


def get(url, **params):
    params["key"] = KEY
    try:
        r = requests.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        print(f"  ✗ 网络错误：{type(exc).__name__}: {exc}")
        return None
    try:
        data = r.json()
    except ValueError:
        print(f"  ✗ 返回的不是 JSON：{r.text[:200]}")
        return None
    if str(data.get("status")) != "1":
        code = str(data.get("infocode"))
        print(f"  ✗ 失败：status={data.get('status')} infocode={code} "
              f"info={data.get('info')}  含义：{ERR.get(code, '见官方文档')}")
        return None
    return data


def main():
    print("=" * 60)
    print("1) 关键词搜索（北京 · 咖啡厅）")
    d = get(TEXT, keywords="咖啡厅", region="北京", page_size=25, page_num=1)
    if not d:
        print("  → key 或服务有问题，先解决这个，后面不用看了")
        return
    pois = d.get("pois", [])
    print(f"  ✓ count={d.get('count')}  本页返回 {len(pois)} 条")

    print("\n2) 周边搜索（国贸 1km 内的咖啡厅，网格法就用它）")
    d2 = get(AROUND, location="116.4612,39.9087", radius=1000, types="050500",
             page_size=25, page_num=1)
    print(f"  {'✓' if d2 else '✗'} "
          f"{'count=' + str(d2.get('count')) if d2 else '（这个接口不通，网格法要换 txt 搜索）'}")

    print("\n3) 返回上限测试（北京市 · 咖啡厅，看 count 会不会被截断）")
    d3 = get(TEXT, keywords="咖啡厅", region="北京市", page_size=25, page_num=1)
    if d3:
        c = int(d3.get("count", 0))
        print(f"  count={c}  → {'超过 1000，必须切网格' if c > 1000 else '未超 1000，但仍建议切网格保证不漏'}")

    print("\n4) 深翻页测试（page_num=5）")
    d4 = get(TEXT, keywords="咖啡厅", region="北京", page_size=25, page_num=5)
    print(f"  {'✓ 第 5 页可用' if d4 and d4.get('pois') else '✗ 第 5 页拿不到（可能限制深翻页）'}")

    print("\n5) 字段完整度（决定模型能不能做）")
    if not pois:
        print("  没有样本，跳过")
        return
    first = pois[0]
    print("  第一条记录的全部字段：")
    print("   ", json.dumps(first, ensure_ascii=False)[:600])
    have = {k: sum(1 for p in pois if p.get(k)) for k in ("rating", "business_area", "typecode", "adname")}
    total = len(pois)
    for k, v in have.items():
        print(f"   {k}: {v}/{total} 有值"
              f"  {'← 缺这个，Huff 的吸引力要换代理变量' if k == 'rating' and v == 0 else ''}")

    print("\n" + "=" * 60)
    print("判定标准：")
    print("  1、2、4 都 ✓，且 rating 有值 ≥ 50%  → 可以当主数据源，方案不变")
    print("  1、2、4 都 ✓，但 rating 基本没有    → 能用，但要改：Huff 吸引力改用『连锁品牌/门店数』代理，")
    print("                                        主结论以『密度 + 最近邻 + 聚类』为主")
    print("  1 就 ✗                              → key/实名/平台类型的问题，按上面的含义先修")


if __name__ == "__main__":
    main()
