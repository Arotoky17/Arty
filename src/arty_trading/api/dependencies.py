"""Dependencies shared by API execution routes."""

from typing import cast

from fastapi import HTTPException, Request

from arty_trading.core.interfaces import IOrderExecutor


def get_executor(request: Request) -> IOrderExecutor:
    """Use the executor selected by the application factory for its trading mode."""
    executor = getattr(request.app.state, "executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Order executor is unavailable")
    return cast(IOrderExecutor, executor)
