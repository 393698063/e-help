from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "e_help.server:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=False,
    )
