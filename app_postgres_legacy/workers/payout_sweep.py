"""
Background worker: releases payouts for orders whose grace window has
passed with no open dispute. Per the architecture spec, this kind of
scheduled/retryable work belongs in a worker, not inline in a request
handler — this runs via APScheduler, wired up in main.py's startup event.

For a heavier production setup, this is the natural place to swap in
Celery + Celery Beat instead of APScheduler, without changing anything in
app/services/escrow.py.
"""
import logging
from app.core.db import AsyncSessionLocal
from app.services.escrow import run_auto_release_sweep

logger = logging.getLogger("savivah.payout_sweep")


async def run_sweep_job():
    async with AsyncSessionLocal() as db:
        try:
            count = await run_auto_release_sweep(db)
            if count:
                logger.info(f"Auto-release sweep: released {count} payout(s)")
        except Exception:
            logger.exception("Auto-release sweep failed")
