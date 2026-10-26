"""Seed a local demo database so the console can be viewed with realistic data.

This is sample data only. Delete data/local.db to start clean.
"""

from __future__ import annotations

import asyncio
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.auth import generate_gateway_key, gateway_key_prefix, hash_gateway_key  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import GatewayKey, Model, Partner, Provider, RequestLog  # noqa: E402
from app.utils import utcnow  # noqa: E402

random.seed(7)


async def main() -> None:
    async with SessionLocal() as session:
        partners = (await session.execute(select(Partner).order_by(Partner.id))).scalars().all()
        if not partners:
            print("No partners found. Run the app once first.")
            return

        dubber = partners[0]
        dubber.name = "Dubber Website"
        dubber.slug = "dubber"
        dubber.markup_multiplier = 1.35
        dubber.monthly_cap_usd = 40.0
        dubber.concurrency_limit = 8
        dubber.rpm_limit = 120

        extra = Partner(
            name="Smoke Partner",
            slug="smoke",
            concurrency_limit=4,
            rpm_limit=60,
            monthly_cap_usd=15.0,
            markup_multiplier=1.5,
            allow_free_tier=False,
            notes="Sample partner created by the demo seed.",
            created_at=utcnow() - timedelta(days=21),
        )
        session.add(extra)
        await session.flush()

        keys = []
        for partner, label in ((dubber, "dubber-prod"), (extra, "smoke-prod")):
            raw = generate_gateway_key()
            key = GatewayKey(
                partner_id=partner.id,
                name=label,
                key_hash=hash_gateway_key(raw),
                key_prefix=gateway_key_prefix(raw),
                is_active=True,
                created_at=utcnow() - timedelta(days=20),
                last_used_at=utcnow() - timedelta(minutes=random.randint(2, 90)),
            )
            session.add(key)
            keys.append((partner, key, raw))

        models = (
            await session.execute(
                select(Model)
                .join(Provider, Model.provider_id == Provider.id)
                .where(Model.is_enabled.is_(True), Provider.is_free_tier.is_(False))
            )
        ).scalars().all()
        if not models:
            models = (await session.execute(select(Model))).scalars().all()
        models = models[:6]

        rows = 0
        for day_offset in range(13, -1, -1):
            volume = random.randint(6, 26) if day_offset > 0 else random.randint(3, 9)
            for _ in range(volume):
                partner, key, _raw = random.choices(keys, weights=[0.75, 0.25])[0]
                model = random.choice(models)
                ts = utcnow() - timedelta(days=day_offset, minutes=random.randint(1, 900))
                prompt_tokens = random.randint(120, 2400)
                completion_tokens = random.randint(60, 900)
                cost = round(
                    (prompt_tokens / 1e6) * model.input_price_per_m
                    + (completion_tokens / 1e6) * model.output_price_per_m,
                    8,
                )
                price = round(cost * partner.markup_multiplier, 8)
                failed = random.random() < 0.055
                cached = (not failed) and random.random() < 0.12

                session.add(
                    RequestLog(
                        request_id=f"req_demo{day_offset:02d}{rows:05d}",
                        gateway_key_id=key.id,
                        partner_id=partner.id,
                        end_user=f"user-{random.randint(100, 140)}" if random.random() < 0.5 else None,
                        created_at=ts,
                        requested_model=random.choice(["auto", model.alias or model.model_name]),
                        provider_id=None if failed else model.provider_id,
                        model_id=None if failed else model.id,
                        status_code=503 if failed else 200,
                        latency_ms=random.randint(180, 3200) if not failed else random.randint(15, 90),
                        prompt_tokens=0 if failed or cached else prompt_tokens,
                        completion_tokens=0 if failed or cached else completion_tokens,
                        cost_usd=0.0 if failed or cached else cost,
                        price_usd=0.0 if failed or cached else price,
                        cached=cached,
                        error="No healthy provider was available." if failed else None,
                        source="playground" if random.random() < 0.06 else "api",
                    )
                )
                rows += 1

        await session.commit()
        print(f"Seeded {rows} request rows across 2 partners and {len(models)} models.")
        for partner, key, raw in keys:
            print(f"  {partner.slug}: {raw}")


if __name__ == "__main__":
    asyncio.run(main())
