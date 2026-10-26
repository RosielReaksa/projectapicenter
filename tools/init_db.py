"""Create the database and run first-run bootstrap. Idempotent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bootstrap import bootstrap  # noqa: E402
from app.core import settings_store  # noqa: E402
from app.db import SessionLocal, dispose_db, init_db  # noqa: E402


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        await bootstrap(session)
        values = await settings_store.load_all(session)
    await dispose_db()
    print(f"Database ready. {len(values)} runtime settings loaded.")


if __name__ == "__main__":
    asyncio.run(main())
