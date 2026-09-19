"""03-studio · AI 对话。

对应 test_plan.md：`CH-01` ~ `CH-12`

对话是**用户唯一的输入端**，也是这条链路上唯一可被无限调用的东西，所以两条边界最要紧：
轮数硬上限（`CH-05`）与消息长度上限（`CH-03`）。两者都必须是「上一轮能落库、
本轮被拒」，而不是等落库了才发现超了。
"""

import pytest

from tests.helpers import (
    assert_route_registered,
    bearer,
    insert_job,
    insert_message,
    login_ok,
    quota_account_for,
    register_customer,
)

pytestmark = pytest.mark.asyncio


async def _chatting_job(db, studio, **over):
    return await insert_job(
        db,
        studio.task_id,
        studio.claim_id,
        studio.customer["id"],
        status="chatting",
        **over,
    )


async def test_ch01_chat_round(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)

    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "我想做一杯夏日柠檬茶"},
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers.get("content-type", ""), (
        f"对话必须流式返回：{r.headers.get('content-type')}"
    )

    rows = await db.fetch(
        "SELECT role, content, model FROM chat_message WHERE job_id = $1 ORDER BY id",
        job_id,
    )
    assert len(rows) == 2, f"一轮对话应落 user + assistant 两行：{len(rows)}"
    assert rows[0]["role"] == "user"
    assert rows[1]["role"] == "assistant"
    assert rows[1]["model"], "assistant 行必须记下模型（换模型要能回溯）"
    assert "先聊聊" in rows[1]["content"], "流式片段要拼成完整消息落库"


async def test_ch02_empty_422(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": ""},
    )
    assert r.status_code == 422, r.text


async def test_ch03_4001_chars_422(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "奶" * 4001},
    )
    assert r.status_code == 422, r.text


async def test_ch04_4000_chars_ok(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "奶" * 4000},
    )
    assert r.status_code == 200, f"4000 字是上限内的合法值：{r.status_code} {r.text}"


async def test_ch05_turn_21_409(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    for i in range(20):
        await insert_message(db, job_id, "user", f"第 {i + 1} 轮问题")
        await insert_message(db, job_id, "assistant", f"第 {i + 1} 轮回答")

    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "第 21 轮"},
    )
    assert r.status_code == 409, f"20 轮封顶，第 21 轮应拒：{r.status_code} {r.text}"

    n = await db.fetchval(
        "SELECT count(*) FROM chat_message WHERE job_id = $1 AND role = 'user'", job_id
    )
    assert n == 20, "被拒的那轮不得落库"


async def test_ch06_chat_before_guard_409(client, studio, db, patch_ai):
    job_id = await insert_job(
        db, studio.task_id, studio.claim_id, studio.customer["id"], status="guarding"
    )
    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "在吗"},
    )
    assert r.status_code == 409, f"预检未过不得对话：{r.status_code} {r.text}"


async def test_ch07_chat_history(client, studio, db, patch_ai):
    job_id = await _chatting_job(db, studio)
    for i in range(4):
        await insert_message(db, job_id, "user", f"问题 {i}")
        await insert_message(db, job_id, "assistant", f"回答 {i}")

    r = await client.get(
        f"/api/jobs/{job_id}/chat", headers=bearer(studio.customer_token)
    )
    assert r.status_code == 200, r.text
    messages = r.json()["messages"]
    assert len(messages) == 8, f"应返回全部 8 条：{len(messages)}"
    assert messages[0]["role"] == "user" and messages[0]["content"] == "问题 0", (
        f"必须按时间升序（首条是问题 0）：{messages[0]}"
    )
    assert messages[-1]["content"] == "回答 3"


async def test_ch08_others_chat_403(client, studio, db, patch_ai):
    await register_customer(client, account="cust9002")
    other = await login_ok(client, "cust9002", "pass1234", fp="fp-x")

    job_id = await _chatting_job(db, studio)
    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(other["access_token"]),
        json={"message": "偷看"},
    )
    assert r.status_code == 403, f"非本人应 403：{r.status_code} {r.text}"

    r = await client.get(
        f"/api/jobs/{job_id}/chat", headers=bearer(other["access_token"])
    )
    assert r.status_code == 403, r.text


async def test_ch09_stream_error_no_half_message(client, studio, db, patch_ai):
    """模型服务报错 → SSE 发 error 事件，**不写半截 assistant 消息**。"""
    patch_ai.chat_pieces = ["好呀", RuntimeError("DeepSeek 502")]
    job_id = await _chatting_job(db, studio)

    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "帮我起个标题"},
    )
    assert "error" in r.text, f"流里必须出现 error 事件：{r.text!r}"

    n = await db.fetchval(
        "SELECT count(*) FROM chat_message WHERE job_id = $1 AND role = 'assistant'",
        job_id,
    )
    assert n == 0, "报错时不得留下半截 assistant 消息（用户会以为 AI 说完了）"


async def test_ch10_disconnect_keeps_state(client, studio, db, patch_ai):
    """客户端读到一半断开 → 状态不被回滚。"""
    job_id = await _chatting_job(db, studio)

    async with client.stream(
        "POST",
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "突然断线"},
    ) as resp:
        assert resp.status_code == 200
        async for _line in resp.aiter_lines():
            break  # 只消费第一片就撤

    status = await db.fetchval(
        "SELECT status FROM content_job WHERE id = $1", job_id
    )
    assert status == "chatting", f"断开不该改变 job 状态：{status}"

    n = await db.fetchval(
        "SELECT count(*) FROM chat_message WHERE job_id = $1 AND role = 'user'", job_id
    )
    assert n == 1, "用户那条消息应保留"


async def test_ch11_insufficient_402_before_stream(client, studio, db, patch_ai):
    """额度不足 → 流开始前就 402，不给一个流到一半才报错的体感。"""
    job_id = await _chatting_job(db, studio)
    await quota_account_for(db, studio.merchant["id"], balance=0, reserved=0)

    r = await client.post(
        f"/api/jobs/{job_id}/chat",
        headers=bearer(studio.customer_token),
        json={"message": "还能聊吗"},
    )
    assert r.status_code == 402, f"额度不足应 402：{r.status_code} {r.text}"
    assert "text/event-stream" not in r.headers.get("content-type", ""), (
        "402 不该是一个已经开跑的流"
    )


async def test_ch12_unknown_job_404(client, studio, patch_ai):
    assert_route_registered("POST", "/api/jobs/{job_id}/chat")
    r = await client.post(
        "/api/jobs/999999/chat",
        headers=bearer(studio.customer_token),
        json={"message": "在吗"},
    )
    assert r.status_code == 404, r.text
