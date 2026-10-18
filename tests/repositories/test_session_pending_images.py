"""待送检图片聚合（pending_user_images）测试：多条消息多图纳入 + 派发水位线。"""
from __future__ import annotations

import pytest

from app.models.session import KIND_TASK_CARD, ROLE_ASSISTANT, ROLE_USER
from app.repositories.session_repo import SessionRepo


@pytest.fixture
def repo(session_factory) -> SessionRepo:
    return SessionRepo(session_factory)


async def _add_user_images(repo: SessionRepo, session_id: str, uris: list[str]) -> None:
    await repo.add_message(
        session_id,
        role=ROLE_USER,
        kind="image",
        content="请看图",
        images=[{"uri": uri, "name": uri.split("/")[-1]} for uri in uris],
    )


@pytest.mark.asyncio
async def test_aggregates_images_across_messages(repo):
    """分批上传：多条含图消息全部纳入，时间正序。"""
    await repo.create("S1", None)
    await _add_user_images(repo, "S1", ["img/a.jpg"])
    await repo.add_message("S1", role=ROLE_ASSISTANT, kind="text", content="收到")
    await _add_user_images(repo, "S1", ["img/b.jpg", "img/c.jpg"])

    images = await repo.pending_user_images("S1")

    assert [i["uri"] for i in images] == ["img/a.jpg", "img/b.jpg", "img/c.jpg"]


@pytest.mark.asyncio
async def test_task_card_is_consumption_boundary(repo):
    """派发后的水位线：任务卡之前的图片不再纳入（已消费）；之后的新图纳入。"""
    await repo.create("S1", None)
    await _add_user_images(repo, "S1", ["img/old1.jpg"])
    await _add_user_images(repo, "S1", ["img/old2.jpg"])
    await repo.add_message("S1", role=ROLE_ASSISTANT, kind=KIND_TASK_CARD, content="{}")
    await _add_user_images(repo, "S1", ["img/new1.jpg"])

    images = await repo.pending_user_images("S1")

    assert [i["uri"] for i in images] == ["img/new1.jpg"]


@pytest.mark.asyncio
async def test_dedupes_repeated_uris(repo):
    """同一 URI 重发只计一次（排除用户重传干扰）。"""
    await repo.create("S1", None)
    await _add_user_images(repo, "S1", ["img/a.jpg"])
    await _add_user_images(repo, "S1", ["img/a.jpg", "img/b.jpg"])

    images = await repo.pending_user_images("S1")

    assert [i["uri"] for i in images] == ["img/a.jpg", "img/b.jpg"]


@pytest.mark.asyncio
async def test_respects_max_images_cap(repo):
    await repo.create("S1", None)
    for i in range(5):
        await _add_user_images(repo, "S1", [f"img/{i}.jpg"])

    images = await repo.pending_user_images("S1", max_images=3)

    assert len(images) == 3
    assert [i["uri"] for i in images] == ["img/0.jpg", "img/1.jpg", "img/2.jpg"]


@pytest.mark.asyncio
async def test_empty_session_returns_empty(repo):
    await repo.create("S1", None)
    assert await repo.pending_user_images("S1") == []
    assert await repo.pending_user_images("NO_SUCH") == []


@pytest.mark.asyncio
async def test_tolerates_dirty_images_json(session_factory, repo):
    """images_json 损坏（非 JSON / 非 list）：跳过不炸，正常图片仍聚合。"""
    from app.repositories.session_repo import SessionMessageRecord as Rec

    await repo.create("S1", None)
    await _add_user_images(repo, "S1", ["img/good.jpg"])

    async with session_factory() as session:
        session.add(
            Rec(
                id="msg-bad-1",
                session_id="S1",
                role=ROLE_USER,
                kind="image",
                content="损坏",
                images_json="not-json{",
                created_at=None,
            )
        )
        session.add(
            Rec(
                id="msg-bad-2",
                session_id="S1",
                role=ROLE_USER,
                kind="image",
                content="非列表",
                images_json='{"uri": "obj-not-list"}',
                created_at=None,
            )
        )
        await session.commit()

    images = await repo.pending_user_images("S1")

    assert [i["uri"] for i in images] == ["img/good.jpg"]
