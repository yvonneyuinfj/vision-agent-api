"""场景目录（决策 52）：只读端点与两份权威表的一致性。

这里守的是"目录可以长、关键词表不能偷偷落后"这条关系：
- 白名单取自目录 keys ⇒ 目录缺一个已可路由的场景，写入就会 400 造不出能力；
- 目录多一个未进关键词表的场景 ⇒ 必须标 auto_recognize=False，否则界面会让人
  以为"登记即可智能路由"。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.routers.scene_router import SCENE_KEYWORDS
from app.api.v1.scene import router as scene_router
from app.core.handlers import register_exception_handlers
from app.services.capability_gate import ALLOWED_SCENES
from app.services.scene_catalog import SCENE_CATALOG


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(scene_router)
    return TestClient(app)


def test_scenes_endpoint_returns_catalog_in_order():
    data = _client().get("/api/v1/scenes").json()["result"]
    assert [i["scene"] for i in data["items"]] == list(SCENE_CATALOG)
    assert data["meta"]["total"] == len(SCENE_CATALOG)
    first = data["items"][0]
    assert {"label", "short", "en", "params", "input", "auto_recognize"} <= set(first)
    assert {i["params"] for i in data["items"]} == {"frozen", "draft"}


def test_write_whitelist_is_the_catalog_keys():
    assert ALLOWED_SCENES == tuple(SCENE_CATALOG)


def test_every_routable_scene_is_in_the_catalog():
    """关键词表里的场景若不在目录，写侧白名单会拒绝它 ⇒ 永远造不出该场景的能力。"""
    assert set(SCENE_KEYWORDS) <= set(SCENE_CATALOG)


def test_auto_recognize_mirrors_the_keyword_table():
    for scene, spec in SCENE_CATALOG.items():
        assert spec.auto_recognize is (scene in SCENE_KEYWORDS), scene
