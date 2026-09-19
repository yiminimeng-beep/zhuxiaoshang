"""SC · 截图识别（保底机制）+ OW-05。

`mismatch_flag` 有**两个独立判据**（spec「明确不做」之外最容易写错的一处）：
① 模型自己没把握（`confidence < 0.70`）；② 模型有把握但与链接侧数据打架。
合并成一个布尔，将来「只信插件」或「只信识别」的调整就无从下手。
"""

from tests.helpers import (
    MAX_SCREENSHOT_BYTES,
    OK_IMAGE_MIME,
    OcrStub,
    bearer,
    insert_snapshot,
    submit_post_ok,
)

SHOT_KEY = "minio://bucket/shot.png"


async def upload_shot(client, token, post_id, **over):
    body = {"image_url": SHOT_KEY, "mime": OK_IMAGE_MIME, "size_bytes": 1024}
    body.update(over)
    return await client.post(
        f"/api/posts/{post_id}/screenshot", headers=bearer(token), json=body
    )


async def active_ocr(db, post_id: int):
    return await db.fetchrow(
        "SELECT * FROM ocr_result WHERE post_id = $1 AND is_active = true", post_id
    )


async def test_sc01_upload_ok(client, db, make_scene, patch_ocr):
    """SC-01 正常上传 → 202 + `{ocr_result_id}`，落库一条 active 的识别结果。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 202, r.text
    ocr_id = r.json()["ocr_result_id"]
    assert ocr_id

    row = await active_ocr(db, post_id)
    assert row["id"] == ocr_id
    assert row["model"] == "qwen3-vl"
    assert row["image_url"] == SHOT_KEY
    assert float(row["confidence"]) == 0.95


async def test_sc02_disguised_text_file(client, db, make_scene, patch_ocr):
    """SC-02 `.txt` 改名成 `.png` → 415（校验**真实** mime，不看后缀）。

    客户端把 `mime` 也一起谎报成 `image/png`，只有 `sniff_mime` 读到的
    真实类型是 `text/plain`。若实现只看客户端自述，这条立刻红。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    patch_ocr.sniff("text/plain")

    r = await upload_shot(
        client, scene.customer_token, post_id, image_url="minio://bucket/fake.png"
    )
    assert r.status_code == 415, r.text
    assert await active_ocr(db, post_id) is None


async def test_sc02b_declared_mime_not_a_whitelisted_image(client, db, make_scene, patch_ocr):
    """SC-02b 自述 `mime=application/pdf` → 415（真实类型对也不认）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    patch_ocr.sniff("image/png")

    r = await upload_shot(
        client, scene.customer_token, post_id, mime="application/pdf"
    )
    assert r.status_code == 415, r.text


async def test_sc02c_sniff_declared_mismatch(client, db, make_scene, patch_ocr):
    """SC-02c 自述 `image/png` 与真实 `image/jpeg` 不符 → 415。

    两张都是合法图片，但「说的和给的不一样」本身就值得拒——它要么是客户端
    有 bug，要么是在试探校验。放行会让日志里的类型永远不可信。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    patch_ocr.sniff("image/jpeg")

    r = await upload_shot(client, scene.customer_token, post_id, mime="image/png")
    assert r.status_code == 415, r.text


async def test_sc03_size_limits(client, db, make_scene, patch_ocr):
    """SC-03 `20MB + 1` → 413；`20MB` 整 → 202（边界含等号）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    too_big = await upload_shot(
        client, scene.customer_token, post_id, size_bytes=MAX_SCREENSHOT_BYTES + 1
    )
    assert too_big.status_code == 413, too_big.text

    exact = await upload_shot(
        client, scene.customer_token, post_id, size_bytes=MAX_SCREENSHOT_BYTES
    )
    assert exact.status_code == 202, exact.text


async def test_sc04_no_numbers_recognized(client, db, make_scene, patch_ocr):
    """SC-04 一个数字都没认出来 → `422`，**不得用 0 兜底**。

    0 互动是一个合法值。用它冒充「没认出来」，商户会把一张糊图当成
    「真人真事、只是没人看」批过去。
    """
    from app.services import ai as ai_service

    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    patch_ocr.push(
        ai_service.OcrVerdict(confidence=0.2, model="qwen3-vl", parsed=None, raw_text="模糊")
    )

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 422, r.text
    assert await active_ocr(db, post_id) is None


async def test_sc05_low_confidence_sets_mismatch(client, db, make_scene, patch_ocr):
    """SC-05 `confidence = 0.69` → `mismatch_flag = true`（模型自己没把握）。"""
    from app.services import ai as ai_service

    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    patch_ocr.push(
        ai_service.OcrVerdict(
            confidence=0.69,
            model="qwen3-vl",
            parsed={"likes": 100, "collects": 20, "comments": 5, "shares": 1},
        )
    )

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 202, r.text
    assert (await active_ocr(db, post_id))["mismatch_flag"] is True


async def test_sc06_disagrees_with_plugin_snapshot(client, db, make_scene, patch_ocr):
    """SC-06 识别 `likes=100`、插件快照 `likes=350` → `mismatch_flag = true`。"""
    from app.services import ai as ai_service

    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await insert_snapshot(db, post_id, likes=350, collects=0, comments=0, shares=0)
    patch_ocr.push(
        ai_service.OcrVerdict(
            confidence=0.95,
            model="qwen3-vl",
            parsed={"likes": 100, "collects": 0, "comments": 0, "shares": 0},
        )
    )

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 202, r.text
    assert (await active_ocr(db, post_id))["mismatch_flag"] is True


async def test_sc07_agrees_with_plugin_snapshot(client, db, make_scene, patch_ocr):
    """SC-07 识别 `likes=100`、插件快照也是 `100` → `mismatch_flag = false`。"""
    from app.services import ai as ai_service

    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await insert_snapshot(db, post_id, likes=100, collects=0, comments=0, shares=0)
    patch_ocr.push(
        ai_service.OcrVerdict(
            confidence=0.95,
            model="qwen3-vl",
            parsed={"likes": 100, "collects": 0, "comments": 0, "shares": 0},
        )
    )

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 202, r.text
    assert (await active_ocr(db, post_id))["mismatch_flag"] is False


async def test_sc08_second_screenshot_deactivates_first(client, db, make_scene, patch_ocr):
    """SC-08 上传第 2 张 → 新的 `is_active=true`，旧的被置回 `false`（不删）。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]

    first = await upload_shot(client, scene.customer_token, post_id)
    second = await upload_shot(
        client, scene.customer_token, post_id, image_url="minio://bucket/shot2.png"
    )
    assert first.status_code == 202 and second.status_code == 202

    rows = await db.fetch(
        "SELECT id, is_active FROM ocr_result WHERE post_id = $1 ORDER BY id", post_id
    )
    assert len(rows) == 2, "旧的那条必须留着（留痕）"
    assert rows[0]["is_active"] is False
    assert rows[1]["is_active"] is True
    assert rows[1]["id"] == second.json()["ocr_result_id"]


async def test_sc09_downgrade_allowed_but_recorded(client, db, make_scene, patch_ocr):
    """SC-09 识别值**低于**已有快照 → 允许（202），但写一条告警事件。

    社媒数据会因删帖回落，降级本身合法；但它也是刷分失败后的常见形态，
    故必须留痕而不是静默。
    """
    from app.services import ai as ai_service

    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await insert_snapshot(db, post_id, likes=350, collects=0, comments=0, shares=0)
    patch_ocr.push(
        ai_service.OcrVerdict(
            confidence=0.95,
            model="qwen3-vl",
            parsed={"likes": 100, "collects": 0, "comments": 0, "shares": 0},
        )
    )

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 202, r.text

    events = await db.fetch(
        "SELECT kind, detail FROM track_event WHERE post_id = $1", post_id
    )
    assert len(events) == 1
    assert events[0]["kind"] == "metric_downgrade"


async def test_sc10_get_before_recognition(client, db, make_scene, patch_ocr):
    """SC-10 识别未完成时 `GET /api/ocr/{post_id}` → 409。

    识别接缝抛超时：没有任何 `ocr_result` 落库，此时「查识别结果」的正确答案
    是 409（还没好），不是 200 空结果、也不是 404。
    """
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    patch_ocr.push(TimeoutError("识别超时"))

    r = await upload_shot(client, scene.customer_token, post_id)
    assert r.status_code == 202, r.text
    assert r.json()["ocr_result_id"] is None

    g = await client.get(f"/api/ocr/{post_id}", headers=bearer(scene.customer_token))
    assert g.status_code == 409, g.text


async def test_sc11_get_result(client, db, make_scene, patch_ocr):
    """SC-11 识别完成后 `GET /api/ocr/{post_id}` → 200，含 parsed / confidence / mismatch_flag。"""
    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await upload_shot(client, scene.customer_token, post_id)

    g = await client.get(f"/api/ocr/{post_id}", headers=bearer(scene.customer_token))
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["parsed"]["likes"] == 100
    assert body["confidence"] == 0.95
    assert body["mismatch_flag"] is False


async def test_ow05_someone_elses_ocr(client, db, make_scene, patch_ocr):
    """OW-05 `GET /api/ocr/{post_id}` 取**别人**的识别结果 → 403。"""
    from tests.helpers import token_for

    scene = await make_scene()
    post_id = (await submit_post_ok(client, scene.customer_token, scene))["post"]["id"]
    await upload_shot(client, scene.customer_token, post_id)

    uid = await db.fetchval(
        "INSERT INTO \"user\" (account, password_hash, role, nickname, status) "
        "VALUES ('ow05other', 'x', 'customer', 'ow05other', 'active') RETURNING id"
    )
    g = await client.get(f"/api/ocr/{post_id}", headers=bearer(token_for(uid)))
    assert g.status_code == 403, g.text
