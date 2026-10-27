"""Emergency maintenance CLI (MAINTENANCE_SPEC §7).

For use when the dashboard is unavailable. Reads the same configuration and
writes the same Redis and PostgreSQL state as the API.

    python -m app.cli maintenance status
    python -m app.cli maintenance on --message "Back soon" --until 2025-01-01T15:30:00Z \\
        --allow-ip 1.2.3.4 --reason "DB migration"
    python -m app.cli maintenance off
    python -m app.cli provider pause <name> --reason "..."
    python -m app.cli provider resume <name>
    python -m app.cli partner suspend <name> --public "..." --reason "..."
    python -m app.cli partner resume <name>

Shell access on the server is the credential: this does not use the web login.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from sqlalchemy import select

from app.db import SessionLocal, dispose_db, init_db
from app.models import MaintenanceHistory, Partner, Provider
from app.utils import utcnow


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        print(f"Could not parse time: {value}", file=sys.stderr)
        raise SystemExit(2) from None


async def _history(session, *, level: str, action: str, target: str | None, reason: str | None):
    session.add(
        MaintenanceHistory(
            created_at=utcnow(),
            user_id=None,
            source="cli",
            level=level,
            target=target,
            action=action,
            reason=reason,
        )
    )
    await session.commit()


async def _redis_ok() -> bool:
    from app.core.store import get_store

    try:
        return await get_store().healthy()
    except Exception:
        return False


async def cmd_status(_args) -> int:
    from app.core import maintenance

    snapshot = await maintenance.get_state(force=True)
    async with SessionLocal() as session:
        from app.models import MaintenanceWindow

        window = await maintenance.upcoming_window(session)
        windows = (
            await session.execute(
                select(MaintenanceWindow)
                .where(MaintenanceWindow.status.in_(["scheduled", "active"]))
                .order_by(MaintenanceWindow.starts_at)
            )
        ).scalars().all()

    g = snapshot.global_state
    d = snapshot.degraded
    print(json.dumps(
        {
            "global": {
                "active": g.active,
                "message": g.message,
                "started_at": g.started_at.isoformat() + "Z" if g.started_at else None,
                "expected_end": g.expected_end.isoformat() + "Z" if g.expected_end else None,
                "allow_ips": g.allow_ips,
            },
            "degraded": {"active": d.active, "restrictions": d.restrictions},
            "paused_providers": sorted(snapshot.paused_providers),
            "suspended_partners": sorted(snapshot.suspended_partners),
            "drain": maintenance.drain_status(snapshot),
            "upcoming_window": window,
            "windows": [
                {
                    "id": w.id,
                    "starts_at": w.starts_at.isoformat() + "Z",
                    "ends_at": w.ends_at.isoformat() + "Z",
                    "status": w.status,
                }
                for w in windows
            ],
        },
        indent=2,
    ))
    return 0


async def cmd_maintenance_on(args) -> int:
    from app.core import maintenance

    if not args.reason:
        print("--reason is required", file=sys.stderr)
        return 2

    async with SessionLocal() as session:
        state = await maintenance.start_global(
            session,
            message=args.message or "The service is temporarily paused for maintenance.",
            expected_end=_parse_dt(args.until),
            allow_ips=list(args.allow_ip or []),
            started_by=None,
        )
        await _history(
            session, level="global", action="on", target=None, reason=args.reason
        )

    if not await _redis_ok():
        print(
            "Warning: Redis is unreachable. The change is stored in PostgreSQL and "
            "instances will pick it up when Redis recovers.",
            file=sys.stderr,
        )
    print(f"Maintenance ON. {state.message}")
    return 0


async def cmd_maintenance_off(args) -> int:
    from app.core import maintenance

    async with SessionLocal() as session:
        await maintenance.stop_global(session)
        await _history(
            session, level="global", action="off", target=None, reason=args.reason
        )
    print("Maintenance OFF.")
    return 0


async def cmd_provider(args) -> int:
    from app.core import maintenance

    async with SessionLocal() as session:
        provider = (
            await session.execute(select(Provider).where(Provider.name == args.name))
        ).scalar_one_or_none()
        if not provider:
            print(f"No provider named {args.name!r}", file=sys.stderr)
            return 1

        paused = args.action == "pause"
        await maintenance.set_provider_paused(session, provider, paused, args.reason)
        await _history(
            session,
            level="provider",
            action="on" if paused else "off",
            target=provider.name,
            reason=args.reason,
        )
    print(f"{provider.name}: {'paused' if paused else 'resumed'}")
    return 0


async def cmd_partner(args) -> int:
    from app.core import maintenance

    async with SessionLocal() as session:
        partner = (
            await session.execute(
                select(Partner).where(
                    (Partner.slug == args.name) | (Partner.name == args.name)
                )
            )
        ).scalar_one_or_none()
        if not partner:
            print(f"No partner named {args.name!r}", file=sys.stderr)
            return 1

        suspended = args.action == "suspend"
        if suspended and not args.reason:
            print("--reason is required when suspending", file=sys.stderr)
            return 2

        await maintenance.set_partner_suspended(
            session, partner, suspended, args.reason, args.public
        )
        await _history(
            session,
            level="partner",
            action="on" if suspended else "off",
            target=partner.slug,
            reason=args.reason,
        )
    print(f"{partner.slug}: {'suspended' if suspended else 'resumed'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway",
        description="Emergency maintenance controls. Writes the same state as the dashboard.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    maint = sub.add_parser("maintenance", help="full maintenance controls")
    maint_sub = maint.add_subparsers(dest="action", required=True)

    maint_sub.add_parser("status", help="show current state").set_defaults(func=cmd_status)

    on = maint_sub.add_parser("on", help="block all API calls")
    on.add_argument("--message", default="")
    on.add_argument("--until", default=None, help="ISO time, e.g. 2025-01-01T15:30:00Z")
    on.add_argument("--allow-ip", action="append", default=[])
    on.add_argument("--reason", required=True)
    on.set_defaults(func=cmd_maintenance_on)

    off = maint_sub.add_parser("off", help="lift maintenance")
    off.add_argument("--reason", default=None)
    off.set_defaults(func=cmd_maintenance_off)

    prov = sub.add_parser("provider", help="pause or resume one provider")
    prov_sub = prov.add_subparsers(dest="action", required=True)
    for action in ("pause", "resume"):
        p = prov_sub.add_parser(action)
        p.add_argument("name")
        p.add_argument("--reason", default=None)
        p.set_defaults(func=cmd_provider, action=action)

    part = sub.add_parser("partner", help="suspend or resume one partner")
    part_sub = part.add_subparsers(dest="action", required=True)
    for action in ("suspend", "resume"):
        p = part_sub.add_parser(action)
        p.add_argument("name", help="partner slug or name")
        p.add_argument("--public", default=None, help="message shown to the partner")
        p.add_argument("--reason", default=None, help="internal reason")
        p.set_defaults(func=cmd_partner, action=action)

    return parser


async def _run(args) -> int:
    await init_db()
    try:
        return await args.func(args)
    finally:
        from app.core.store import close_store

        await close_store()
        await dispose_db()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
