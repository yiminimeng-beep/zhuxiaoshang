"""`admin_action_log` 的**唯一**写入点。

四类动作共用这同一条 INSERT：07 的额度记账、06 的封禁解封、06 的内容与截图
异常处理、04 的申诉裁决。散成四份写法的直接后果是字段名会漂——而这张表正是
「事后翻账」时唯一能对的东西（谁是 admin、动了什么、前后什么样）。

**只 INSERT**：这张表上不存在 UPDATE / DELETE 的代码路径。审计表改历史
就等于改证据，所以连一个「修改日志」的入口都不留。

`detail` 收一个 dict 而不是拼好的字符串：调账的前后余额、封禁的理由、
裁决的批注，形状本来就不同，塞进字符串会退化成「调用方自己解析」。
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def log_action(
    session: AsyncSession,
    *,
    admin_id: int,
    action: str,
    target_type: str,
    target_id: int,
    detail: dict | None = None,
) -> None:
    """写一条审计日志。不 commit——与调用方的业务写入同属一个事务。

    同事务是刻意的：动作生效而日志没落（或反过来）都会让审计说谎。
    """
    await session.execute(
        text(
            "INSERT INTO admin_action_log "
            "(admin_id, action, target_type, target_id, detail, created_at) "
            "VALUES (:admin, :action, :target_type, :target, "
            "CAST(:detail AS jsonb), now())"
        ),
        {
            "admin": admin_id,
            "action": action,
            "target_type": target_type,
            "target": target_id,
            "detail": (
                json.dumps(detail, ensure_ascii=False) if detail is not None else None
            ),
        },
    )
