# web · 实时数据看板（零第三方依赖）

## 启动

```powershell
cd "E:\DSH工作区\数据分析选题"
python web\server.py            # http://127.0.0.1:5000
python web\server.py 8080       # 换端口
```

打开浏览器访问提示的地址即可。**按 Ctrl+C 停止。**

## 页面

| 地址 | 内容 |
|---|---|
| `/` | 总览：门店总数、咖啡数、密度、评分覆盖、连锁率、人均中位 + 分布图 + 品类构成 + 最新 12 家 |
| `/stores?city=深圳&kw=星巴克&page=1` | 门店列表：搜索（门店名/区/商圈）+ 分页 |
| `/api/summary?city=深圳` | JSON 接口，给别人/别的程序用 |
| `/chart.png?city=深圳` | 密度图（数据变了会自动重画） |

## "实时"是怎么做到的

1. **每次请求都重新读 `data/{城市}_stores.csv`**，不缓存、不常驻内存 → 你重新跑一次采集，刷新页面就是新数据，**不用重启服务**；
2. 页面顶部显示 **数据时间**（CSV 文件的修改时间），一眼看出数据新旧；
3. 总览页 **每 30 秒自动刷新**（改 `server.py` 里的 `REFRESH_SECONDS`）；
4. 响应头带 `Cache-Control: no-store`，浏览器不缓存旧页面。

## 数据从哪来

```powershell
python amap_poi.py --city 深圳      # 采集 → data/深圳_stores.csv
python analysis.py                  # 可选：出分析结论和图
python web\server.py                # 看板
```

没有数据时，首页会提示"还没有数据，先跑一次采集"。

## 依赖

- **只用 Python 标准库**（`http.server`），不需要 Flask；
- 画图用 `matplotlib`（没装则图不显示，其余功能正常）；
- 中文图需要系统有「微软雅黑」或「黑体」。

## 注意

- 默认只监听 `127.0.0.1`，**不要直接改成 `0.0.0.0` 暴露到公网**——没有鉴权，任何人都能看到；
- 需要多人在线看，建议用 `waitress`（Windows 友好）或 Nginx 反代，并加访问控制；
- 页面无 JS 框架，纯服务端渲染，给答辩演示够用，给生产系统不够。
