"""前端同源托管冒烟：用 TestClient 直测 app.main 的路由（不跑 lifespan，无需 MySQL）。
验证点：
  1) GET /            → 200 且直出 index.html（Vue3 深蓝版特征：/assets/ 引用）
  2) GET /login       → 200 且与 index.html 字节一致（SPA history catch-all，不 404）
  3) GET /detection/1 → 200（嵌套深链同样回落）
  4) GET /assets/xxx.js → 200 真实静态文件
  5) GET /assets/不存在.js → 404（带扩展名未命中不把 index.html 当 JS 返回）
"""
import sys

from fastapi.testclient import TestClient

from app.main import app, FRONTEND_DIST

index_bytes = (FRONTEND_DIST / "index.html").read_bytes()
ok = True


def check(name: str, cond: bool, extra: str = "") -> None:
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    ok = ok and cond


client = TestClient(app)  # 不进 with：跳过 lifespan，纯验路由层

r = client.get("/")
check("GET / 直出 index.html", r.status_code == 200 and r.content == index_bytes,
      f"status={r.status_code}")
check("首页为 Vue3 产物（含 /assets/ 引用与 #app 挂载点）",
      b"/assets/" in r.content and b'id="app"' in r.content)

r = client.get("/login")
check("GET /login 深链不 404 且回 index.html",
      r.status_code == 200 and r.content == index_bytes, f"status={r.status_code}")

r = client.get("/detection/ABC123")
check("嵌套深链 /detection/ABC123 同样回落", r.status_code == 200 and r.content == index_bytes,
      f"status={r.status_code}")

import re
asset = re.search(rb'src="(/assets/[^"]+\.js)"', index_bytes)
if asset:
    p = asset.group(1).decode()
    r = client.get(p)
    check(f"静态资源 {p} 直出", r.status_code == 200 and "javascript" in r.headers.get("content-type", ""),
          f"status={r.status_code} ct={r.headers.get('content-type')}")
else:
    check("index.html 中能解析出 js 资源引用", False)

r = client.get("/assets/__definitely_missing__.js")
check("缺失带扩展名资源 → 404", r.status_code == 404, f"status={r.status_code}")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
