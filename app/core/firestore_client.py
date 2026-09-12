"""
Firestore is now the single source of truth (replacing PostgreSQL). This
module is the only place that constructs the Firestore client — everything
else imports `db` from here.
"""
from google.cloud.firestore import AsyncClient
from google.oauth2 import service_account
from app.core.config import settings

if settings.FIREBASE_CREDENTIALS_PATH:
    credentials = service_account.Credentials.from_service_account_file(settings.FIREBASE_CREDENTIALS_PATH)
    db = AsyncClient(project=settings.FIREBASE_PROJECT_ID, credentials=credentials)
else:
    # Falls back to Application Default Credentials — e.g. the
    # GOOGLE_APPLICATION_CREDENTIALS env var pointing at a key file, or an
    # emulator via FIRESTORE_EMULATOR_HOST during local development.
    db = AsyncClient(project=settings.FIREBASE_PROJECT_ID)


def get_db():
    return db
