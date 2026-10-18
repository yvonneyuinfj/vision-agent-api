"""mock_service 单进程六缺陷域测试：borescope + infrared + dr + fpi + fracture + metallography。

同一 app 同时服务系统六场景域；文件名关键词路由缺陷类型
（fatigue_crack 含子串 "crack"，fracture 组关键词必须先于
borescope 组匹配）。infrared/dr/fpi 无专属缺陷池，仅域名声明。
每个用例用 with TestClient(app) 触发 lifespan（否则 state.model_ready=False，
/health 与 inference 均为 503）。
"""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

# 1x1 透明 PNG：有 PIL 时可解码，无 PIL 时走形式校验，两种环境行为一致
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def infer(filename: str, parameters: str | None = None, scene: str | None = None) -> dict:
    import mock_service.main as mod

    data: dict = {
        "task_id": "T-test",
        "parameters": parameters or "{}",
    }
    if scene:
        data["scene"] = scene
    with TestClient(mod.app) as client:
        resp = client.post(
            "/api/v1/inference",
            files={"image": (filename, PNG_BYTES, "image/png")},
            data=data,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["result"]


def infer_multi(filenames: list[str], parameters: str | None = None) -> dict:
    """多图提交：同名字段 image 重复出现（平台 http_adapter 的发送形态）。"""
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        resp = client.post(
            "/api/v1/inference",
            files=[("image", (fn, PNG_BYTES, "image/png")) for fn in filenames],
            data={"task_id": "T-multi", "parameters": parameters or "{}"},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["result"]


def test_health_declares_all_defect_domains():
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["defect_domains"] == ["borescope", "infrared", "dr", "fpi", "fracture", "metallography"]


@pytest.mark.parametrize(
    ("filename", "expected_type", "expected_cn"),
    [
        ("fatigue_sample.jpg", "fatigue_crack", "疲劳裂纹"),
        ("cleavage_sample.jpg", "cleavage", "解理"),
        ("dimple_sample.jpg", "dimple", "韧窝"),
        ("intergranular_sample.jpg", "intergranular", "沿晶"),
    ],
)
def test_fracture_primary_defect_by_filename(filename, expected_type, expected_cn):
    result = infer(filename)
    assert result["status"] == "abnormal"
    assert result["defects"][0]["defect_type"] == expected_type
    assert result["defects"][0]["defect_name_cn"] == expected_cn


@pytest.mark.parametrize(
    ("filename", "expected_type", "expected_cn"),
    [
        ("grain_sample.jpg", "grain_coarsening", "晶粒粗大"),
        ("inclusion_sample.jpg", "inclusion", "夹杂"),
        ("decarburization_sample.jpg", "decarburization", "脱碳"),
        ("segregation_sample.jpg", "segregation", "偏析"),
    ],
)
def test_metallography_primary_defect_by_filename(filename, expected_type, expected_cn):
    result = infer(filename)
    assert result["status"] == "abnormal"
    assert result["defects"][0]["defect_type"] == expected_type
    assert result["defects"][0]["defect_name_cn"] == expected_cn


def test_metallography_keyword_triggers_abnormal():
    result = infer("metallography_sample.png")
    assert result["status"] == "abnormal"


def test_mock_status_normal_overrides_filename():
    result = infer("fatigue_abnormal.jpg", parameters=json.dumps({"mock_status": "normal"}))
    assert result["status"] == "normal"
    assert result["defects"] == []


def test_borescope_defect_types_unchanged():
    result = infer("crack_abnormal.jpg")
    assert result["status"] == "abnormal"
    assert result["defects"][0]["defect_type"] == "crack"
    assert result["defects"][0]["defect_name_cn"] == "裂纹"


def test_fatigue_crack_does_not_collide_with_crack_keyword():
    # "fatigue_crack" 含子串 "crack"：fracture 关键词优先匹配，不得落回 borescope 裂纹
    result = infer("fatigue_crack_sample.jpg")
    assert result["defects"][0]["defect_type"] == "fatigue_crack"


def test_invalid_parameters_still_rejected():
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        resp = client.post(
            "/api/v1/inference",
            files={"image": ("fatigue.jpg", PNG_BYTES, "image/png")},
            data={"task_id": "T-test", "parameters": "not-json"},
        )
    assert resp.status_code == 422
    assert resp.json()["code"] == 2001


def test_scene_default_defect_for_metallography():
    # 文件名无缺陷关键词 + 强制 abnormal + scene=metallography → 回退金相默认缺陷，
    # 不得落回 borescope 的 crack（用户诉求：金相场景默认回金相缺陷类型）
    result = infer("class4_1.jpg", parameters=json.dumps({"mock_status": "abnormal"}), scene="metallography")
    assert result["status"] == "abnormal"
    assert result["defects"][0]["defect_type"] == "grain_coarsening"
    assert result["defects"][0]["defect_name_cn"] == "晶粒粗大"


def test_scene_default_keeps_crack_without_scene():
    # 回归：无 scene 字段时保持全局默认 crack
    result = infer("class4_1.jpg", parameters=json.dumps({"mock_status": "abnormal"}))
    assert result["defects"][0]["defect_type"] == "crack"


def test_filename_keyword_beats_scene_default():
    # 关键词路由优先于场景默认：金相场景 + 含 inclusion 关键词文件名 → inclusion
    result = infer("inclusion_01.jpg", parameters=json.dumps({"mock_status": "abnormal"}), scene="metallography")
    assert result["defects"][0]["defect_type"] == "inclusion"


def test_multi_image_each_inferred_and_merged():
    # 多图逐张推理：两张异常图各自的缺陷都进结果，defect_id 全局连号
    result = infer_multi(
        ["crack_abnormal.jpg", "grain_abnormal.jpg"],
        parameters=json.dumps({"max_defects": 1}),
    )
    assert result["status"] == "abnormal"
    types = {d["defect_type"] for d in result["defects"]}
    assert "crack" in types and "grain_coarsening" in types
    assert [d["defect_id"] for d in result["defects"]] == ["D001", "D002"]
    # 描述携带来源图名（多图溯源）
    assert any("crack_abnormal.jpg" in d["description"] for d in result["defects"])
    assert any("grain_abnormal.jpg" in d["description"] for d in result["defects"])


def test_multi_image_mixed_normal_and_abnormal():
    # 一张异常一张正常：仅异常图产缺陷，整体 status=abnormal
    result = infer_multi(["burn_abnormal.jpg", "clean.jpg"])
    assert result["status"] == "abnormal"
    assert len(result["defects"]) >= 1


def test_multi_image_all_normal():
    result = infer_multi(["clean_1.jpg", "clean_2.jpg"])
    assert result["status"] == "normal"
    assert result["defects"] == []


def test_multi_image_invalid_member_rejected():
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        resp = client.post(
            "/api/v1/inference",
            files=[
                ("image", ("ok.jpg", PNG_BYTES, "image/png")),
                ("image", ("bad.txt", b"not-an-image", "text/plain")),
            ],
            data={"task_id": "T-multi", "parameters": "{}"},
        )
    assert resp.status_code == 415
    assert resp.json()["code"] == 1002


def test_abnormal_results_vary_across_calls():
    """随机化回归：同一请求参数重复调用，abnormal 结果内容不得全部相同。"""
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        rendered = set()
        for _ in range(10):
            resp = client.post(
                "/api/v1/inference",
                files={"image": ("crack_repeat.jpg", PNG_BYTES, "image/png")},
                data={"task_id": "T-vary", "parameters": '{"max_defects": 3}'},
            )
            assert resp.status_code == 200, resp.text
            rendered.add(json.dumps(resp.json()["result"], sort_keys=True))

    assert len(rendered) > 1


@pytest.mark.parametrize("severity", ["low", "medium", "high"])
def test_mock_severity_overrides_all_defects(severity):
    # 联调参数：确定性覆盖全部缺陷严重度（平台判级/复核门演示需要可复现）
    result = infer("cleavage_sample.jpg", parameters=json.dumps({"mock_severity": severity}))
    assert result["status"] == "abnormal"
    assert result["defects"]
    assert all(d["severity"] == severity for d in result["defects"])


def test_mock_severity_invalid_value_rejected():
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        resp = client.post(
            "/api/v1/inference",
            files={"image": ("cleavage.jpg", PNG_BYTES, "image/png")},
            data={"task_id": "T-test", "parameters": json.dumps({"mock_severity": "critical"})},
        )
    assert resp.status_code == 422
    assert resp.json()["code"] == 2001


def test_mock_error_short_circuits_inference_with_model_failure():
    # 联调参数：短路推理模拟模型侧故障（平台失败链路/错误报告演示）
    import mock_service.main as mod

    with TestClient(mod.app) as client:
        resp = client.post(
            "/api/v1/inference",
            files={"image": ("cleavage.jpg", PNG_BYTES, "image/png")},
            data={"task_id": "T-test", "parameters": json.dumps({"mock_error": True})},
        )
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == 3002
    assert body["result"] is None