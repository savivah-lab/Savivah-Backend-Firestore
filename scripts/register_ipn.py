"""
Run once during setup: python scripts/register_ipn.py
Prints the ipn_id to paste into your .env as PESAPAL_IPN_ID.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from app.services import pesapal


async def main():
    ipn_url = settings.PESAPAL_CALLBACK_URL.replace("/callback", "/ipn")
    result = await pesapal.register_ipn_url(ipn_url)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
