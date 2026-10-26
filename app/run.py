"""Start the gateway: `python -m app.run` or `uvicorn app.main:app`."""

from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=settings.environment != "production",
        log_level="info",
    )


if __name__ == "__main__":
    main()
