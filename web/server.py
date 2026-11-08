# -*- coding: utf-8 -*-
"""网页服务：展示最新采集的数据（零第三方依赖，只用标准库）

启动：
    python web/server.py                默认 http://127.0.0.1:5000
    python web/server.py 8080           换端口

页面：
    /                     总览（自动每 30 秒刷新，显示数据时间）
    /stores?city=深圳&kw=&page=1   门店列表（搜索 + 分页）
    /api/summary?city=深圳          JSON 接口（给前端/别人调用）
    /chart.png?city=深圳            密度图（按需生成，数据变了自动重画）

为什么"实时"：每次请求都重新读 data/{city}_stores.csv，页面上显示文件修改时间。
你重新跑一次抓取，刷新页面就是新数据，不用重启服务。
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from data_access import cities, data_time, density_png, search, summary

HOST, DEFAULT_PORT = "127.0.0.1", 5000
REFRESH_SECONDS = 30          # 页面自动刷新间隔

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
<h3 style="margin:18px 0 8px">门店分布</h3>
<img src="/chart.png?city={city}&t={s['data_time']}" alt="分布图">
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
