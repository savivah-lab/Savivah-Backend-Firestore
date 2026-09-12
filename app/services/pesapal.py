"""
Pesapal API 3.0 client. Same real endpoints as the previous Node
implementation (verified against Pesapal's own docs) — this is a port, not
a rewrite from assumptions.

Flow:
  1. authenticate()        -> bearer token (~5 min lifetime, cached)
  2. register_ipn_url()    -> run ONCE at setup (see scripts/register_ipn.py)
  3. submit_order_request() -> called at checkout, returns a redirect_url
  4. Pesapal redirects the customer to callback_url AND calls our IPN
  5. get_transaction_status() -> called from both the callback route and the
     IPN webhook to confirm the real status (callback/IPN params never carry
     status themselves — this is explicit in Pesapal's own documentation)
"""
import time
import httpx
from app.core.config import settings

_cached_token: str | None = None
_cached_token_expiry: float = 0


async def authenticate() -> str:
    global _cached_token, _cached_token_expiry
    if _cached_token and time.time() < _cached_token_expiry - 15:
        return _cached_token

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.pesapal_base_url}/api/Auth/RequestToken",
            json={
                "consumer_key": settings.PESAPAL_CONSUMER_KEY,
                "consumer_secret": settings.PESAPAL_CONSUMER_SECRET,
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        data = resp.json()

    if "token" not in data:
        raise RuntimeError(f"Pesapal auth failed: {data.get('error', {}).get('message', data)}")

    _cached_token = data["token"]
    # expiryDate is an ISO string; store as a rough epoch estimate (5 min from now is safe)
    _cached_token_expiry = time.time() + 280
    return _cached_token


async def register_ipn_url(ipn_url: str) -> dict:
    """Run once during setup — NOT called automatically per-order. See
    scripts/register_ipn.py. Save the returned ipn_id as PESAPAL_IPN_ID."""
    token = await authenticate()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.pesapal_base_url}/api/URLSetup/RegisterIPN",
            json={"url": ipn_url, "ipn_notification_type": "POST"},
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        return resp.json()


async def submit_order_request(
    merchant_reference: str,
    amount: float,
    description: str,
    email: str,
    phone: str | None,
    first_name: str,
    last_name: str,
) -> dict:
    """merchant_reference must be unique per attempt: alphanumeric, -, _, ., : only, max 50 chars."""
    token = await authenticate()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.pesapal_base_url}/api/Transactions/SubmitOrderRequest",
            json={
                "id": merchant_reference,
                "currency": "KES",
                "amount": amount,
                "description": description[:100],
                "callback_url": settings.PESAPAL_CALLBACK_URL,
                "notification_id": settings.PESAPAL_IPN_ID,
                "billing_address": {
                    "email_address": email,
                    "phone_number": phone,
                    "country_code": "KE",
                    "first_name": first_name,
                    "last_name": last_name,
                },
            },
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"SubmitOrderRequest failed: {data['error'].get('message')}")
    return data  # { order_tracking_id, merchant_reference, redirect_url }


async def get_transaction_status(order_tracking_id: str) -> dict:
    token = await authenticate()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.pesapal_base_url}/api/Transactions/GetTransactionStatus",
            params={"orderTrackingId": order_tracking_id},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        )
        return resp.json()
    # status_code: 0 INVALID, 1 COMPLETED, 2 FAILED, 3 REVERSED
