# -*- coding: utf-8 -*-
"""网页服务：展示最新采集的数据 + 地图点选选址评估（零第三方依赖，只用标准库）

启动：
    python web/server.py                默认 http://127.0.0.1:5000
    python web/server.py 8080           换端口

页面：
    /                     总览（自动刷新）+ 地图点选评估
    /stores?city=深圳&kw=&page=1   门店列表（搜索 + 分页）
    /api/summary?city=深圳          JSON：汇总
    /api/site?city=深圳&lng=114.06&lat=22.53   JSON：该点适不适合开店
    /chart.png?city=深圳            密度图（按需生成）

为什么"实时"：每次请求都重新读 data/{city}_stores.csv，页面上显示文件修改时间。
你重新跑一次抓取，刷新页面就是新数据，不用重启服务。
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import site_eval
from data_access import BBOX, cities, data_time, density_png, load, search, summary

HOST, DEFAULT_PORT = "127.0.0.1", 5000
REFRESH_SECONDS = 30          # 页面自动刷新间隔
STATIC_DIR = Path(__file__).resolve().parent / "static"   # leaflet 等本地静态文件

CSS = """
body{font-family:"Microsoft YaHei",Arial,sans-serif;margin:0;background:#f6f7f9;color:#222}
header{background:#1f2d3d;color:#fff;padding:14px 22px;display:flex;gap:18px;align-items:center}
header b{font-size:18px}
nav a{color:#cfd8e3;text-decoration:none;margin-right:14px}
nav a:hover{color:#fff}
main{max-width:1080px;margin:18px auto;padding:0 16px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:12px 16px;min-width:150px}
.card .k{color:#6b7785;font-size:12px}
.card .v{font-size:22px;font-weight:700;margin-top:4px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e6ea;border-radius:8px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid #eef0f3;font-size:13px;text-align:left}
th{background:#f2f4f7;color:#44505f}
tr:hover td{background:#fafbfc}
.hint{color:#6b7785;font-size:13px;margin:8px 0}
.warn{background:#fff6e5;border:1px solid #ffd591;padding:12px 14px;border-radius:8px}
form{display:flex;gap:8px;margin:12px 0}
input,select,button{padding:7px 10px;border:1px solid #ccd2da;border-radius:6px;font-size:13px}
button{background:#1f2d3d;color:#fff;border:0;cursor:pointer}
a.pager{display:inline-block;padding:5px 10px;border:1px solid #ccd2da;border-radius:6px;color:#1f2d3d;text-decoration:none;margin-right:6px;font-size:13px}
img{max-width:100%;border:1px solid #e3e6ea;border-radius:8px;background:#fff}
.mapbox{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:14px 16px;margin:18px 0}
.mapwrap{display:flex;gap:16px;flex-wrap:wrap}
#map{flex:2;min-width:420px;height:420px;border:1px solid #d6dbe2;border-radius:8px;cursor:crosshair;background:#f0f3f7}
.verdict{flex:1;min-width:260px;max-width:420px;font-size:13px;line-height:1.7}
.verdict ul{padding-left:18px;margin:8px 0}
.verdict li{margin-bottom:6px}
.score{font-size:20px;font-weight:700;margin-bottom:4px}
.score span{font-size:14px;color:#44505f;font-weight:400}
.legend{font-size:12px;color:#5a6673;margin-top:10px}
.legend i{margin-right:4px}
"""


def page(title, body, city=None, auto_refresh=False):
    refresh = f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' if auto_refresh else ""
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
{refresh}<title>{title}</title><style>{CSS}</style></head><body>
<header><b>咖啡选址 · 实时数据</b><nav>
  <a href="/">总览</a>
  <a href="/stores">门店列表</a>
  <a href="/api/summary">API</a>
</nav><span style="margin-left:auto;font-size:12px;color:#cfd8e3">
  数据时间：{data_time(city) if city else "—"} ｜ 页面每 {REFRESH_SECONDS} 秒刷新
</span></header><main>{body}</main></body></html>"""


def map_section(city):
    """地图点选评估：Leaflet + 高德底图，点一下调 /api/site。"""
    b = BBOX.get(city)
    rows = load(city)
    if not b or not rows:
        return '<div class="warn">还没有数据，无法显示地图。先跑一次采集。</div>'

    # 静态文件在不在，先自己检查一遍（比让浏览器报 404 更早发现问题）
    missing = [n for n in ("leaflet.js", "leaflet.css")
               if not (STATIC_DIR / n).is_file()]
    if missing:
        return f"""<div class="mapbox"><div class="warn">
<b>缺静态文件：{', '.join(missing)}</b><br>
服务端在 <code>{STATIC_DIR}</code> 找不到它们，所以地图加载不了。<br>
修复：确认 <code>web\\static\\leaflet.js</code> 和 <code>leaflet.css</code> 存在；
没有的话运行 <code>python tools\\fetch_leaflet.py</code> 重新下载，或从别的副本拷过来。<br>
<span class="hint">当前城市数据：{len(rows)} 条（地图不受影响，只是画不出来）</span>
</div></div>"""

    colors = {"咖啡": "#c0392b", "地铁站": "#2980b9", "写字楼": "#95a5a6",
              "商场": "#8e44ad", "酒店": "#16a085", "景区": "#d35400"}
    points, counts = [], {}
    for r in rows:
        cat = r.get("category")
        if cat not in colors or r.get("lng_f") is None:
            continue
        counts[cat] = counts.get(cat, 0) + 1
        points.append([round(r["lat_f"], 6), round(r["lng_f"], 6), cat,
                       (r.get("name") or "")[:24]])

    legend = "".join(
        f'<span style="margin-right:14px"><i style="display:inline-block;width:9px;'
        f'height:9px;background:{c};border-radius:50%"></i>{k} {counts.get(k, 0)}</span>'
        for k, c in colors.items())

    # 地图初始视野：落在咖啡店最密集的位置（用中位数，抗离群）
    coffee_pts = [p for p in points if p[2] == "咖啡"]
    if coffee_pts:
        lats = sorted(p[0] for p in coffee_pts)
        lngs = sorted(p[1] for p in coffee_pts)
        center = [lats[len(lats) // 2], lngs[len(lngs) // 2]]
    else:
        center = [(b["lat_min"] + b["lat_max"]) / 2, (b["lng_min"] + b["lng_max"]) / 2]

    js = """
<link rel="stylesheet" href="/static/leaflet.css?v=1">
<script>
// 只报真正的脚本错误；图片/资源加载失败不算（否则图表 404 会误报）
window.addEventListener('error', function (ev) {
  if (!ev.message) return;
  var v = document.getElementById('verdict');
  if (v) v.innerHTML = '<div class="warn">页面脚本出错：' + (ev.message || ev.type) +
      '<br>打开 F12 控制台看详细信息。</div>';
}, true);
</script>
<script src="/static/leaflet.js?v=1"></script>
<script>
const CITY = __CITY__, BBOX = __BBOX__, COLORS = __COLORS__, POINTS = __POINTS__;
const verdict = document.getElementById('verdict');
const mapEl = document.getElementById('map');

if (typeof L === 'undefined') {
  // 自己把真实原因查出来：是 404、还是网络失败
  mapEl.innerHTML = '<div style="padding:24px;color:#b3261e;line-height:1.8">' +
    '<b>Leaflet 没加载成功</b><br>正在检查 /static/leaflet.js …</div>';
  fetch('/static/leaflet.js?v=1', {cache: 'no-store'}).then(function (r) {
    var tip = r.status === 404
      ? '服务端没有这个文件：确认 web/static/leaflet.js 存在（不存在就运行 python tools/fetch_leaflet.py），<br>或者服务还是旧进程 —— 按 Ctrl+C 重新 python web/server.py。'
      : '按 Ctrl+F5 强刷一次；还不行就把这段截图发我。';
    mapEl.innerHTML = '<div style="padding:24px;color:#b3261e;line-height:1.8">' +
      '<b>Leaflet 没加载成功</b><br>' +
      '/static/leaflet.js 返回 <b>HTTP ' + r.status + '</b><br>' + tip + '</div>';
  }).catch(function (e) {
    mapEl.innerHTML = '<div style="padding:24px;color:#b3261e;line-height:1.8">' +
      '<b>Leaflet 没加载成功</b><br>请求 /static/leaflet.js 出错：' + e + '</div>';
  });
  verdict.innerHTML = '<div class="warn">地图脚本未加载，评估不可用。</div>';
} else {
  const map = L.map('map', {zoomControl: true, preferCanvas: true});
  map.setView(__CENTER__, 12);
  if (POINTS.length) {
    // 自动缩放到所有点：一次看到全城，而不是只看到中心那一小块
    map.fitBounds(L.latLngBounds(POINTS.map(function (p) { return [p[0], p[1]]; })),
                  {padding: [24, 24], maxZoom: 13});
  }

  const tiles = L.tileLayer(
    'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    {subdomains: ['1', '2', '3', '4'], maxZoom: 19, minZoom: 9,
     attribution: '© 高德地图（GCJ-02）'});
  tiles.on('tileerror', function () {
    if (!window.__tileWarned) {
      window.__tileWarned = 1;
      verdict.innerHTML = '<div class="warn">底图瓦片加载失败（可能网络受限）。' +
        '点位和点选评估仍然可用。</div>';
    }
  });
  tiles.addTo(map);

  // 设施点位：用 canvas 渲染，上万点也不卡
  const canvas = L.canvas({padding: 0.5});
  const big = POINTS.length > 3000;      // 点多的时候不绑 tooltip，避免卡顿
  POINTS.forEach(function (p) {
    const m = L.circleMarker([p[0], p[1]], {
      renderer: canvas,
      radius: p[2] === '咖啡' ? 3.5 : 2.6,
      color: COLORS[p[2]], fillColor: COLORS[p[2]],
      fillOpacity: 0.75, weight: 0.5
    });
    if (!big) { m.bindTooltip(p[2] + (p[3] ? '：' + p[3] : '')); }
    m.addTo(map);
  });

  let pick = null;
  map.on('click', async function (e) {
    const lat = e.latlng.lat, lng = e.latlng.lng;
    if (pick) { map.removeLayer(pick); }
    pick = L.circleMarker([lat, lng], {radius: 9, color: '#111', weight: 2,
                                       fillColor: '#fff', fillOpacity: 0.9}).addTo(map);

    verdict.innerHTML = '<div class="hint">计算中…</div>';
    try {
      const url = '/api/site?city=' + encodeURIComponent(CITY) +
                  '&lng=' + lng.toFixed(6) + '&lat=' + lat.toFixed(6);
      const d = await (await fetch(url)).json();
      if (!d.ok) { verdict.innerHTML = '<div class="warn">' + d.error + '</div>'; return; }
      const html =
        '<div class="score" style="color:' + d.colour + '">' + d.level +
        ' <span>' + (d.score * 100).toFixed(0) + ' 分</span></div>' +
        '<div class="hint">位置 ' + d.lng.toFixed(4) + ', ' + d.lat.toFixed(4) + '</div>' +
        '<ul>' + d.reasons.map(function (x) { return '<li>' + x + '</li>'; }).join('') + '</ul>' +
        '<div class="hint">需求分位 ' + (d.demand.percentile * 100).toFixed(0) +
        '% ｜ 竞争分位 ' + (d.competition.percentile * 100).toFixed(0) +
        '% ｜ 最近同类 ' + d.competition.nearest_m + ' m</div>';
      verdict.innerHTML = html;
      L.popup().setLatLng([lat, lng]).setContent(html).openOn(map);
    } catch (err) {
      verdict.innerHTML = '<div class="warn">请求失败：' + err + '</div>';
    }
  });
}
</script>"""

    js = (js.replace("__CITY__", json.dumps(city, ensure_ascii=False))
            .replace("__BBOX__", json.dumps(b))
            .replace("__COLORS__", json.dumps(colors, ensure_ascii=False))
            .replace("__POINTS__", json.dumps(points, ensure_ascii=False))
            .replace("__CENTER__", json.dumps(center)))

    return f"""
<div class="mapbox">
  <h3 style="margin:0 0 6px">🗺️ 选址评估：在地图上点一个位置</h3>
  <p class="hint">底图是高德（与点位同为 GCJ-02 坐标，不会偏移）。
     点任意位置，用已有的咖啡店 / 地铁站 / 写字楼 / 商场数据判断适不适合开店。</p>
  <div class="mapwrap">
    <div id="map"></div>
    <div id="verdict" class="verdict">点击地图上的任意位置开始评估。</div>
  </div>
  <div class="legend">{legend}</div>
</div>
{js}"""


def chart_block(city, summary_data):
    """分布图：matplotlib 不可用时给出明确提示，而不是一个破图。"""
    try:
        png = density_png(city)
    except Exception as exc:
        return (f'<div class="warn">分布图生成失败：{type(exc).__name__}: {exc}</div>')
    if not png:
        return ('<div class="warn">分布图暂时不可用：<br>'
                '① 没装 matplotlib（<code>pip install matplotlib</code>）；或 '
                '② 这个城市还没有咖啡数据。</div>')
    return (f'<img src="/chart.png?city={city}&t={summary_data["data_time"]}" '
            f'alt="{city} 咖啡门店分布">')


def render_home(qs):
    cs = cities()
    if not cs:
        return page("咖啡选址", """
<div class="warn"><b>还没有数据。</b><br>
先跑一次采集：<code>python amap_poi.py --city 深圳</code>，
完成后刷新本页即可看到数据（不用重启服务）。</div>""")

    city = (qs.get("city") or [cs[0]])[0]
    if city not in cs:
        city = cs[0]
    s = summary(city)

    opts = "".join(
        f'<option value="{c}"{" selected" if c == city else ""}>{c}</option>' for c in cs)
    cards = f"""
<div class="cards">
  <div class="card"><div class="k">门店总数</div><div class="v">{s['total']}</div></div>
  <div class="card"><div class="k">咖啡门店</div><div class="v">{s['coffee']}</div></div>
  <div class="card"><div class="k">门店密度</div><div class="v">{s['density']:.2f}<span style="font-size:12px"> 家/km²</span></div></div>
  <div class="card"><div class="k">评分覆盖</div><div class="v">{s['rating_pct']:.0%}</div></div>
  <div class="card"><div class="k">连锁率</div><div class="v">{s['chain_pct']:.0%}</div></div>
  <div class="card"><div class="k">人均中位</div><div class="v">{s['median_cost'] or '—'}<span style="font-size:12px"> 元</span></div></div>
</div>""" if not s.get("empty") else ""

    cats = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>"
                   for k, v in (s.get("by_cat") or {}).items())
    rows, total = search(city, "", 1, 12)
    latest = "".join(
        f"<tr><td>{r.get('name','')}</td><td>{r.get('category','')}</td>"
        f"<td>{r.get('adname','')}</td><td>{r.get('business_area','')}</td>"
        f"<td>{r.get('rating','') or '—'}</td><td>{r.get('cost','') or '—'}</td></tr>"
        for r in rows)

    body = f"""
<form method="get" action="/">
  <select name="city" onchange="this.form.submit()">{opts}</select>
  <span class="hint">当前城市：{city} ｜ 数据时间：{s['data_time']}</span>
</form>
{cards}
{map_section(city)}
<h3 style="margin:18px 0 8px">门店分布</h3>
{chart_block(city, s)}
<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:18px">
  <div style="flex:1;min-width:280px">
    <h3>品类构成</h3>
    <table><tr><th>品类</th><th>数量</th></tr>{cats or '<tr><td colspan=2>无</td></tr>'}</table>
  </div>
  <div style="flex:2;min-width:360px">
    <h3>最新 12 家门店</h3>
    <table><tr><th>名称</th><th>品类</th><th>区</th><th>商圈</th><th>评分</th><th>人均</th></tr>
    {latest or '<tr><td colspan=6>无</td></tr>'}</table>
    <p class="hint"><a href="/stores?city={city}">查看全部 {total} 条 →</a></p>
  </div>
</div>"""
    return page(f"{city} · 咖啡选址", body, city=city, auto_refresh=True)


def render_stores(qs):
    cs = cities()
    if not cs:
        return page("门店列表", '<div class="warn">还没有数据，先跑一次采集。</div>')
    city = (qs.get("city") or [cs[0]])[0]
    if city not in cs:
        city = cs[0]
    kw = (qs.get("kw") or [""])[0]
    try:
        p = max(1, int((qs.get("page") or ["1"])[0]))
    except ValueError:
        p = 1
    size = 25
    rows, total = search(city, kw, p, size)
    pages = max(1, (total + size - 1) // size)

    opts = "".join(f'<option value="{c}"{" selected" if c == city else ""}>{c}</option>'
                   for c in cs)
    trs = "".join(
        f"<tr><td>{r.get('name','')}</td><td>{r.get('category','')}</td>"
        f"<td>{r.get('adname','')}</td><td>{r.get('business_area','')}</td>"
        f"<td>{r.get('rating','') or '—'}</td><td>{r.get('cost','') or '—'}</td></tr>"
        for r in rows)

    def link(n, label=None):
        return (f'<a class="pager" href="/stores?city={city}&kw={kw}&page={n}">'
                f'{label or n}</a>')

    pager = "".join(link(n) for n in range(max(1, p - 2), min(pages, p + 2) + 1))
    body = f"""
<form method="get" action="/stores">
  <select name="city" onchange="this.form.submit()">{opts}</select>
  <input name="kw" value="{kw}" placeholder="搜门店名 / 区 / 商圈">
  <button>搜索</button>
  <span class="hint">共 {total} 条，第 {p}/{pages} 页</span>
</form>
<table><tr><th>名称</th><th>品类</th><th>区</th><th>商圈</th><th>评分</th><th>人均</th></tr>
{trs or '<tr><td colspan=6>没有匹配结果</td></tr>'}</table>
<p style="margin-top:12px">{'«' if p > 1 else ''} {pager} {'»' if p < pages else ''}</p>"""
    return page(f"{city} · 门店列表", body, city=city)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[web] {self.address_string()} {fmt % args}")

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")     # 实时：不缓存
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                self._send(200, render_home(qs))
            elif path == "/stores":
                self._send(200, render_stores(qs))
            elif path == "/api/summary":
                cs = cities()
                city = (qs.get("city") or [cs[0] if cs else ""])[0]
                self._send(200, json.dumps(summary(city), ensure_ascii=False, indent=2),
                           "application/json; charset=utf-8")
            elif path.startswith("/static/"):
                name = path[len("/static/"):]
                fp = STATIC_DIR / name
                ok = (".." not in name and name and fp.is_file()
                      and STATIC_DIR.resolve() in fp.resolve().parents)
                if not ok:
                    self._send(404, f"static not found: {name}", "text/plain; charset=utf-8")
                else:
                    ctype = ("text/css" if name.endswith(".css")
                             else "application/javascript" if name.endswith(".js")
                             else "application/octet-stream")
                    self._send(200, fp.read_bytes(), f"{ctype}; charset=utf-8")
            elif path == "/api/diag":
                cs = cities()
                info = {
                    "static_dir": str(STATIC_DIR),
                    "static_files": {n: (STATIC_DIR / n).is_file()
                                     for n in ("leaflet.js", "leaflet.css")},
                    "data_dir": str(site_eval.DATA),
                    "cities": {c: len(site_eval.load(c)) for c in cs},
                }
                self._send(200, json.dumps(info, ensure_ascii=False, indent=2),
                           "application/json; charset=utf-8")
            elif path == "/api/site":
                cs = cities()
                city = (qs.get("city") or [cs[0] if cs else ""])[0]
                try:
                    lng = float((qs.get("lng") or ["0"])[0])
                    lat = float((qs.get("lat") or ["0"])[0])
                except ValueError:
                    self._send(400, json.dumps({"ok": False, "error": "经纬度不合法"},
                                               ensure_ascii=False),
                               "application/json; charset=utf-8")
                    return
                result = site_eval.evaluate(city, lng, lat)
                self._send(200, json.dumps(result, ensure_ascii=False),
                           "application/json; charset=utf-8")
            elif path == "/chart.png":
                cs = cities()
                city = (qs.get("city") or [cs[0] if cs else ""])[0]
                png = density_png(city)
                if not png:
                    self._send(404, "no chart", "text/plain; charset=utf-8")
                else:
                    self._send(200, png.read_bytes(), "image/png")
            else:
                self._send(404, page("404", "<h3>页面不存在</h3><p><a href='/'>回首页</a></p>"))
        except Exception as exc:                      # 页面永不 500 白屏
            self._send(500, page("出错了", f"<div class='warn'>"
                                           f"{type(exc).__name__}: {exc}</div>"))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = ThreadingHTTPServer((HOST, port), Handler)
    print(f"服务已启动： http://{HOST}:{port}")
    print(f"数据目录： {DATA_DIR}")
    print("按 Ctrl+C 停止")
    srv.serve_forever()


if __name__ == "__main__":
    from data_access import DATA as DATA_DIR
    main()
