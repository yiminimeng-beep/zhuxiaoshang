"""03-studio · 归属、下载、删除。

对应 test_plan.md：`OW-01` ~ `OW-15`

两条不能含糊的：

- `OW-01`：跨用户一律 **403 而不是 404**——404 会把「这个 id 存不存在」透出去，
  等于送一个 id 枚举接口。与 01/02/07 是同一条规矩。
- `OW-15`：软删**只标记一行**。子行与对象存储里的东西都不动，否则「保留 15 分钟的
  下载链接」会在删除后立刻 404，用户正在下的文件会断在中途。
"""

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    insert_asset,
    insert_job,
    insert_message,
    insert_output,
    insert_reservation,
    login_ok,
    quota_account_for,
    register_customer,
)

pytestmark = pytest.mark.asyncio


async def _ready_job(db, studio, **over):
    row = {"status": "ready"}
    row.update(over)
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], **row
    )
    output_id = await insert_output(db, job_id)
    return job_id, output_id


async def _other_token(client):
    await register_customer(client, account="cust9002")
    body = await login_ok(client, "cust9002", "pass1234", fp="fp-x")
    return body["access_token"]


async def test_ow01_cross_user_403_not_404(client, studio, db):
    job_id, _ = await _ready_job(db, studio)
    token = await _other_token(client)

    r = await client.get(f"/api/jobs/{job_id}", headers=bearer(token))
    assert r.status_code == 403, (
        f"跨用户必须 403，不得用 404 泄露 id 是否存在：{r.status_code} {r.text}"
    )


async def test_ow02_download_others_403(client, studio, db):
    job_id, output_id = await _ready_job(db, studio)
    token = await _other_token(client)

    r = await client.get(
        f"/api/jobs/{job_id}/download/{output_id}", headers=bearer(token)
    )
    assert r.status_code == 403, f"不得下载别人的产物：{r.status_code} {r.text}"


async def test_ow03_not_ready_409(client, studio, db):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="generating"
    )
    output_id = await insert_output(db, job_id)

    r = await client.get(
        f"/api/jobs/{job_id}/download/{output_id}",
        headers=bearer(studio.customer_token),
    )
    assert r.status_code == 409, f"没就绪不得下载：{r.status_code} {r.text}"


async def test_ow04_download_url_15min(client, studio, db, patch_presign):
    job_id, output_id = await _ready_job(db, studio)
    r = await client.get(
        f"/api/jobs/{job_id}/download/{output_id}",
        headers=bearer(studio.customer_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"], "必须给出下载地址"
    assert body["expires_in"] == 900, (
        f"spec 定的是 15 分钟有效：{body['expires_in']}"
    )
    assert patch_presign.calls, "下载必须走预签名，不能是永久直链"
    assert patch_presign.calls[-1]["expires_in"] == 900, (
        f"传给对象存储的有效期也要是 900 秒：{patch_presign.calls[-1]}"
    )


async def test_ow05_reissue_new_url(client, studio, db, patch_presign):
    """重新申请 → 再签一次（不缓存旧链接）。"""
    job_id, output_id = await _ready_job(db, studio)
    url = f"/api/jobs/{job_id}/download/{output_id}"

    r1 = await client.get(url, headers=bearer(studio.customer_token))
    r2 = await client.get(url, headers=bearer(studio.customer_token))
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(patch_presign.calls) == 2, (
        f"两次申请应签两次：{patch_presign.calls}"
    )


async def test_ow06_output_not_in_job_404(client, studio, db, patch_presign):
    assert_route_registered("GET", "/api/jobs/{job_id}/download/{output_id}")
    job_a, _ = await _ready_job(db, studio)
    _, output_b = await _ready_job(db, studio)

    r = await client.get(
        f"/api/jobs/{job_a}/download/{output_b}",
        headers=bearer(studio.customer_token),
    )
    assert r.status_code == 404, (
        f"产物不属于该 job 应 404：{r.status_code} {r.text}"
    )


async def test_ow07_soft_delete_created(client, studio, db):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="created"
    )
    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 204, f"created 态应可删：{r.status_code} {r.text}"

    row = await db.fetchrow(
        "SELECT deleted_at FROM content_job WHERE id = $1", job_id
    )
    assert row is not None, "软删不物理删行"
    assert row["deleted_at"] is not None, "必须有 deleted_at 标记"


async def test_ow08_delete_guard_failed_ok(client, studio, db):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"],
        status="guard_failed",
    )
    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 204, f"预检失败的 job 应可删：{r.status_code} {r.text}"


async def test_ow09_delete_ready_409(client, studio, db):
    job_id, _ = await _ready_job(db, studio)
    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 409, f"已产出作品不得删：{r.status_code} {r.text}"


async def test_ow10_delete_need_review_409(client, studio, db):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"],
        status="need_review",
    )
    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 409, f"转人工的 job 不得删：{r.status_code} {r.text}"


async def test_ow11_deleted_detail_404(client, studio, db):
    assert_route_registered("GET", "/api/jobs/{job_id}")
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="created"
    )
    await client.delete(f"/api/jobs/{job_id}", headers=bearer(studio.customer_token))

    r = await client.get(f"/api/jobs/{job_id}", headers=bearer(studio.customer_token))
    assert r.status_code == 404, f"删掉后本人也查不到：{r.status_code} {r.text}"


async def test_ow12_double_delete_404(client, studio, db):
    assert_route_registered("DELETE", "/api/jobs/{job_id}")
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="created"
    )
    await client.delete(f"/api/jobs/{job_id}", headers=bearer(studio.customer_token))
    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 404, f"重复删除应 404：{r.status_code} {r.text}"


async def test_ow13_delete_others_403(client, studio, db):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="created"
    )
    token = await _other_token(client)
    r = await client.delete(f"/api/jobs/{job_id}", headers=bearer(token))
    assert r.status_code == 403, r.text

    deleted = await db.fetchval(
        "SELECT deleted_at FROM content_job WHERE id = $1", job_id
    )
    assert deleted is None, "被拒的删除不得留下任何痕迹"


async def test_ow14_delete_releases_reservation(client, studio, db):
    """删除未结算的 job → 释放预占，**不写流水**。"""
    await quota_account_for(
        db, studio.merchant["id"], balance=100_000, reserved=120
    )
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="created"
    )
    await insert_reservation(
        db, job_id, studio.merchant["id"], studio.customer["id"], studio.task_id,
        reserved=120,
    )

    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 204, r.text

    status = await db.fetchval(
        "SELECT status FROM quota_reservation WHERE job_id = $1", job_id
    )
    assert status == "released", f"预占单应释放：{status}"

    reserved = await db.fetchval(
        "SELECT reserved FROM quota_account WHERE user_id = $1", studio.merchant["id"]
    )
    assert reserved == 0, f"预占要真的退回去：{reserved}"

    n = await db.fetchval(
        "SELECT count(*) FROM quota_ledger WHERE job_id = $1", job_id
    )
    assert n == 0, "释放不写流水（靠预扣单状态审计）"


async def test_ow15_delete_keeps_rows(client, studio, db):
    """软删只标记 job 一行；素材、对话、对象存储都不动。"""
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="created"
    )
    await insert_asset(db, job_id)
    await insert_message(db, job_id, "user", "删我之前说的话")

    r = await client.delete(
        f"/api/jobs/{job_id}", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 204, r.text

    assets = await db.fetchval(
        "SELECT count(*) FROM job_input_asset WHERE job_id = $1", job_id
    )
    messages = await db.fetchval(
        "SELECT count(*) FROM chat_message WHERE job_id = $1", job_id
    )
    assert assets == 1, "素材行不得随软删级联消失"
    assert messages == 1, "对话记录不得随软删级联消失"
