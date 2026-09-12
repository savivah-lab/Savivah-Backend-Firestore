"""
Verifies a Google Identity Services ID token from the frontend's "Continue
with Google" button, against Google's own servers — not just decoded and
trusted locally.
"""
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from app.core.config import settings

_google_request = google_requests.Request()


def verify_google_token(token: str) -> dict:
    """Raises ValueError if the token is invalid, expired, or doesn't match
    our Client ID. Returns the decoded payload (email, name, sub, picture, ...)."""
    return id_token.verify_oauth2_token(token, _google_request, settings.GOOGLE_CLIENT_ID)
