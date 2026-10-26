"""First-run setup: owner account, default provider catalog, demo partner."""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.core.auth import hash_password
from app.models import Model, Partner, Provider, User
from app.providers.registry import PROVIDERS
from app.utils import utcnow

log = logging.getLogger("gateway.bootstrap")


async def ensure_owner(session: AsyncSession) -> None:
    count = (await session.execute(select(func.count()).select_from(User))).scalar() or 0
    if count:
        return

    email = (settings.admin_email or "admin@localhost").lower().strip()
    password = settings.admin_password
    generated = False

    if not password:
        password = secrets.token_urlsafe(16)
        generated = True

    session.add(
        User(
            email=email,
            role="owner",
            password_hash=hash_password(password),
            created_at=utcnow(),
        )
    )
    await session.commit()

    if generated:
        path = DATA_DIR / "first-run.txt"
        path.write_text(
            "Private LLM Gateway - first run\n"
            "===============================\n"
            f"Email:    {email}\n"
            f"Password: {password}\n\n"
            "Sign in, change this password, and enable two-factor authentication.\n"
            "Then delete this file.\n",
            encoding="utf-8",
        )
        log.warning(
            "Created the owner account %s with a generated password (see %s).",
            email,
            path,
        )
    else:
        log.info("Created the owner account %s from ADMIN_EMAIL/ADMIN_PASSWORD.", email)


async def seed_providers(session: AsyncSession) -> None:
    existing = {name for (name,) in (await session.execute(select(Provider.name))).all()}
    added = 0
    for seed in PROVIDERS:
        if seed.name in existing:
            continue
        provider = Provider(
            name=seed.name,
            label=seed.label,
            base_url=seed.base_url,
            is_free_tier=seed.free_tier,
            may_train_on_prompts=seed.free_tier,  # conservative default, editable in the UI
            priority=seed.priority,
            is_active=True,
            created_at=utcnow(),
        )
        session.add(provider)
        await session.flush()
        for model in seed.models:
            session.add(
                Model(
                    provider_id=provider.id,
                    model_name=model.name,
                    alias=model.alias,
                    context_length=model.context_length,
                    input_price_per_m=model.input_price_per_m,
                    output_price_per_m=model.output_price_per_m,
                    supports_vision=model.supports_vision,
                    supports_tools=model.supports_tools,
                    prices_checked_at=utcnow(),
                )
            )
        added += 1

    if added:
        await session.commit()
        log.info("Seeded %d providers with their starter models.", added)


async def ensure_default_partner(session: AsyncSession) -> None:
    """A first partner so keys can be created immediately after setup."""
    count = (await session.execute(select(func.count()).select_from(Partner))).scalar() or 0
    if count:
        return
    session.add(
        Partner(
            name="Default",
            slug="default",
            concurrency_limit=10,
            rpm_limit=120,
            monthly_cap_usd=20.0,
            markup_multiplier=1.0,
            allow_free_tier=False,
            created_at=utcnow(),
        )
    )
    await session.commit()
    log.info("Created the default partner.")


async def bootstrap(session: AsyncSession) -> None:
    await ensure_owner(session)
    if settings.seed_demo_data:
        await seed_providers(session)
        await ensure_default_partner(session)
