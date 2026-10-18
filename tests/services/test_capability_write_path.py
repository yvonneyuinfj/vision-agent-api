"""能力写侧端点行为回归（决策 27/34/36/41/42/43/45/49-③）。

不碰 DB、不碰 HTTP：仓储与注册中心用替身，探针直接 monkeypatch。
验收口径是 MySQL，本文件只锁服务层语义（回执台账 / 分层门禁 / 列级 patch 回显），
不做 SQLite 双库比对。
"""
from __future__ import annotations

import pytest

from app.core.errors import BizCode, BusinessError
from app.models.capability import CapabilityPatchBody, CapabilityStatusBody, DryRunBody
from app.repositories.capability_repo import STATUS_DISABLED, STATUS_ENABLED, CapabilityRecord
from app.services import capability_service as cs


def _record(**over):
    base = dict(
        capability_id="cap_a",
        tool_name="tool_a",
        scene="fpi",
        capability_type="defect_detection",
        adapter_type="http",
        endpoint="http://127.0.0.1:18016",
        health_endpoint=None,
        tool_version="1.0.0",
        model_version="1.0.0",
        api_version="1.0.0",
        input_schema=None,
        output_schema=None,
        timeout_s=60,
        max_retry=2,
        status=STATUS_ENABLED,
        description="d",
        model_weights_sha256=None,
        meta_json='{"headers": {"X-API-Key": "secret-abcd"}}',
        version=7,
    )
    base.update(over)
    return CapabilityRecord(**base)


class _FakeRepo:
    def __init__(self, rec: CapabilityRecord) -> None:
        self.rec = rec
        self.writes: list[dict] = []

    async def get(self, capability_id: str):
        return self.rec if capability_id == self.rec.capability_id else None

    async def update_fields(self, capability_id, fields, expected_version=None):  # noqa: ANN001
        if expected_version is not None and expected_version != self.rec.version:
            return 0
        self.writes.append({"capability_id": capability_id, "fields": fields})
        self.rec.version += 1
        return 1

    async def insert(self, **kwargs):  # noqa: ANN003
        self.writes.append(kwargs)
        return True


class _FakeRegistry:
    def __init__(self) -> None:
        self.refreshes = 0

    def list(self):
        return []

    def list_enabled(self):
        return []

    async def refresh(self):
        self.refreshes += 1
        return 0


@pytest.fixture(autouse=True)
def _clean_ledger():
    cs.reset_receipts()
    yield
    cs.reset_receipts()


def _service(rec: CapabilityRecord | None = None):
    record = rec or _record()
    repo, registry = _FakeRepo(record), _FakeRegistry()
    return cs.CapabilityService(registry, repo), repo, registry, record


def _stub_probes(monkeypatch, svc, *, healthy=True, sample=None):
    async def health(endpoint, health_url, headers, timeout_s):
        return (healthy, "HTTP 200，1ms", 1) if healthy else (False, "ConnectError", None)

    async def inference(body, headers):
        if sample is None:
            return False, "HTTP 500", None, None
        return True, "HTTP 200，2ms", 2, sample

    async def artifacts(sample_, headers, timeout_s):
        return True, "无 artifacts，跳过"

    monkeypatch.setattr(svc, "_probe_health", health)
    monkeypatch.setattr(svc, "_probe_inference", inference)
    monkeypatch.setattr(svc, "_probe_artifacts", artifacts)


CANONICAL = {"code": 0, "result": {"status": "normal", "defects": [], "summary": "s"}}


# ---------- 干跑：连通性 ≠ 完整试运行（决策 34/41） ----------
async def test_connectivity_run_passes_but_issues_no_receipt(monkeypatch):
    svc, *_ = _service()
    _stub_probes(monkeypatch, svc)
    result = await svc.dry_run(DryRunBody(endpoint="http://127.0.0.1:18016", headers={}, image_path=""))

    assert result.passed is True
    assert result.snapshot == ""
    assert result.steps[1].message == "未填样例物料：本次仅为连通性检查，不产生保存回执"
    assert not cs.has_receipt(cs.snapshot_of("http://127.0.0.1:18016", {}))


async def test_full_run_issues_receipt_in_ledger(monkeypatch):
    svc, *_ = _service()
    _stub_probes(monkeypatch, svc, sample=CANONICAL)
    body = DryRunBody(
        endpoint="http://127.0.0.1:18016",
        headers={"X-API-Key": "secret-abcd"},
        image_path="local-tools/fluorescence/server-bundle/test01.jpg",
    )
    result = await svc.dry_run(body)

    assert result.passed is True and result.snapshot
    assert cs.has_receipt(result.snapshot)
    assert [s.name for s in result.steps] == ["health", "inference", "artifacts"]


async def test_health_failure_skips_inference_and_stays_unpaid(monkeypatch):
    svc, *_ = _service()
    _stub_probes(monkeypatch, svc, healthy=False)
    result = await svc.dry_run(
        DryRunBody(endpoint="http://127.0.0.1:18016", image_path="local-tools/fluorescence/server-bundle/test01.jpg")
    )
    assert result.passed is False
    assert result.steps[1].message == "健康探测未通过，跳过"
    assert result.snapshot == ""


# ---------- 保存门禁：指纹相符还不够，必须有回执 ----------
async def test_patch_blocked_when_snapshot_matches_but_no_receipt(monkeypatch):
    rec = _record(endpoint="http://127.0.0.1:18016")
    svc, repo, _, _ = _service(rec)
    submitted_headers = {"X-API-Key": "secret-abcd"}
    forged = cs.snapshot_of("http://127.0.0.1:18107", submitted_headers)

    with pytest.raises(BusinessError) as exc:
        await svc.patch(
            "cap_a",
            CapabilityPatchBody(
                expected_version=rec.version, change_reason="改端点",
                endpoint="http://127.0.0.1:18107", expected_snapshot=forged,
            ),
        )
    assert "没有对应的成功完整试运行记录" in str(exc.value.message)
    assert repo.writes == []


async def test_patch_allowed_after_full_run(monkeypatch):
    rec = _record(endpoint="http://127.0.0.1:18016")
    svc, repo, registry, _ = _service(rec)
    _stub_probes(monkeypatch, svc, sample=CANONICAL)
    headers = {"X-API-Key": "secret-abcd"}
    result = await svc.dry_run(
        DryRunBody(
            endpoint="http://127.0.0.1:18107", headers=headers,
            image_path="local-tools/fluorescence/server-bundle/test01.jpg",
        )
    )

    ack = await svc.patch(
        "cap_a",
        CapabilityPatchBody(
            expected_version=rec.version, change_reason="切换到 18107",
            endpoint="http://127.0.0.1:18107", meta={"headers": headers},
            expected_snapshot=result.snapshot,
        ),
    )
    assert ack["fields"] == ["endpoint", "meta.headers"]
    assert repo.rec.version == 8 and registry.refreshes == 1
    logged = repo.writes[0]["fields"]["meta_json"]["change_log"][0]
    assert logged["fields"] == ["endpoint", "meta.headers"]


async def test_optimistic_lock_conflict_before_gate(monkeypatch):
    rec = _record(endpoint="http://127.0.0.1:18016", version=3)
    svc, repo, _, _ = _service(rec)
    with pytest.raises(BusinessError) as exc:
        await svc.patch(
            "cap_a",
            CapabilityPatchBody(expected_version=99, change_reason="撞车", endpoint="http://127.0.0.1:1"),
        )
    assert exc.value.code is BizCode.TASK_STATE_CONFLICT
    assert repo.writes == []


async def test_write_without_any_field_is_rejected():
    rec = _record()
    svc, _, _, _ = _service(rec)
    with pytest.raises(BusinessError) as exc:
        await svc.patch("cap_a", CapabilityPatchBody(expected_version=rec.version, change_reason="空提交"))
    assert "没有任何可写字段" in str(exc.value.message)


# ---------- 回显口径：meta_json 是落库载体，不是用户改动的列 ----------
async def test_meta_only_patch_reports_subkeys_not_meta_json():
    rec = _record(status=STATUS_DISABLED)
    svc, repo, _, _ = _service(rec)
    ack = await svc.patch(
        "cap_a",
        CapabilityPatchBody(
            expected_version=rec.version, change_reason="补默认参数",
            meta={"default_params": {"part_id": "UNKNOWN"}},
        ),
    )
    assert ack["fields"] == ["meta.default_params"]
    assert "meta_json" not in ack["fields"]
    assert repo.writes[0]["fields"]["meta_json"]["default_params"] == {"part_id": "UNKNOWN"}


async def test_column_patch_only_touches_submitted_columns():
    rec = _record(status=STATUS_DISABLED, timeout_s=60, health_endpoint="http://127.0.0.1:18016/health")
    svc, repo, _, _ = _service(rec)
    ack = await svc.patch(
        "cap_a",
        CapabilityPatchBody(
            expected_version=rec.version, change_reason="健康端点回退为派生默认", health_endpoint=None
        ),
    )
    assert ack["fields"] == ["health_endpoint"]
    assert set(repo.writes[0]["fields"]) == {"health_endpoint", "meta_json"}


# ---------- 启停（决策 45）：启用前置健康探测，未过不落库 ----------
async def test_enable_blocked_by_health_probe(monkeypatch):
    rec = _record(status=STATUS_DISABLED)
    svc, repo, _, _ = _service(rec)

    async def failing_health(endpoint, health_url, headers, timeout_s):
        return False, "ConnectError", None

    monkeypatch.setattr(svc, "_probe_health", failing_health)
    with pytest.raises(BusinessError) as exc:
        await svc.set_status("cap_a", CapabilityStatusBody(status=STATUS_ENABLED, change_reason="上线"))
    assert exc.value.code is BizCode.MODEL_NOT_READY
    assert repo.writes == []


async def test_disable_is_written_without_probe(monkeypatch):
    rec = _record(status=STATUS_ENABLED)
    svc, repo, registry, _ = _service(rec)

    async def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("停用不该探测健康")

    monkeypatch.setattr(svc, "_probe_health", boom)
    ack = await svc.set_status("cap_a", CapabilityStatusBody(status=STATUS_DISABLED, change_reason="现场检修"))
    assert repo.writes[0]["fields"]["status"] == STATUS_DISABLED
    assert registry.refreshes == 1
    assert "tool not registered" in ack["note"]


# ---------- 样例物料钉死在仓库内（决策 33）：用 tmp 仓库根，不依赖交付包在位 ----------
def _fake_repo_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "BASE_DIR", tmp_path)
    for rel in ("local-tools/a.jpg", "local-tools/b.opi", "data/c.png"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    (tmp_path / "elsewhere" / "d.jpg").write_bytes(b"x")


def test_sample_paths_pinned_to_repo_and_support_multi_frame(tmp_path, monkeypatch):
    _fake_repo_root(tmp_path, monkeypatch)
    resolved = cs._resolve_sample_images("local-tools/a.jpg,\nlocal-tools/b.opi data/c.png")
    assert [p.name for p in resolved] == ["a.jpg", "b.opi", "c.png"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("elsewhere/d.jpg", "必须位于"),
        ("local-tools/a.txt", "扩展名不支持"),
        ("local-tools/missing.jpg", "不存在"),
        ("../local-tools/a.jpg", "不存在"),
        ("", "至少提供一个"),
    ],
)
def test_sample_path_rejects_outside_root_unknown_ext_and_missing(tmp_path, monkeypatch, raw: str, expected: str):
    _fake_repo_root(tmp_path, monkeypatch)
    with pytest.raises(BusinessError) as exc:
        cs._resolve_sample_images(raw)
    assert expected in str(exc.value.message)


# ---------- 详情：读方向只回掩码（决策 43） ----------
async def test_detail_masks_headers_and_exposes_lock_watermark():
    rec = _record(meta_json='{"headers": {"X-API-Key": "super-secret-abcd"}}')
    svc, _, _, _ = _service(rec)
    detail = await svc.detail("cap_a")
    assert detail.headers_masked == {"X-API-Key": "****abcd"}
    assert "super-secret" not in detail.model_dump_json()
    assert detail.version == rec.version
    assert detail.snapshot == cs.snapshot_of(rec.endpoint, {"X-API-Key": "super-secret-abcd"})
