"""
Central configuration. Every environment variable the backend needs is
declared here, once, with a clear required/optional split.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Required: server refuses to start without these (see main.py) ---
    DATABASE_URL: str
    JWT_SECRET: str
    ADMIN_JWT_SECRET: str  # deliberately separate signing key from customer/seller tokens

    # --- Server ---
    PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:5173"
    ADMIN_FRONTEND_URL: str = "http://localhost:5174"

    # --- Redis (cache + rate limiting; NEVER authoritative for money/stock) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    PRODUCT_CACHE_TTL_SECONDS: int = 30

    # --- Pesapal ---
    PESAPAL_ENV: str = "sandbox"  # "sandbox" | "live"
    PESAPAL_CONSUMER_KEY: str = ""
    PESAPAL_CONSUMER_SECRET: str = ""
    PESAPAL_CALLBACK_URL: str = ""
    PESAPAL_IPN_ID: str = ""

    # --- Google sign-in ---
    GOOGLE_CLIENT_ID: str = ""

    # --- Fargo ---
    FARGO_API_KEY: str = ""
    FARGO_WEBHOOK_SECRET: str = ""

    # --- Token lifetimes ---
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15          # customer/seller — short-lived per spec
    ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES: int = 10     # admin — even shorter
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- Rate limiting ---
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 8

    @property
    def pesapal_base_url(self) -> str:
        return "https://pay.pesapal.com/v3" if self.PESAPAL_ENV == "live" else "https://cybqa.pesapal.com/pesapalv3"


settings = Settings()
