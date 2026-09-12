import logging
from app.core.firestore_client import get_db
from app.services.escrow import run_auto_release_sweep

logger = logging.getLogger("savivah.payout_sweep")


async def run_sweep_job():
    db = get_db()
    try:
        count = await run_auto_release_sweep(db)
        if count:
            logger.info(f"Auto-release sweep: released {count} payout(s)")
    except Exception:
        logger.exception("Auto-release sweep failed")
