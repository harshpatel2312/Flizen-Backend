import firebase_admin
from firebase_admin import credentials, firestore
from app.core.config import settings
from app.core.logging import get_logger
import os

logger = get_logger(__name__)

# Initialize Firebase
def init_firebase():
    """Initialize Firebase Admin SDK"""
    try:
        firebase_admin.get_app()
    except ValueError:
        creds_path = settings.FIREBASE_CREDENTIALS_PATH
        if os.path.exists(creds_path):
            cred = credentials.Certificate(creds_path)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase initialised from %s", creds_path)
        else:
            logger.error("Firebase credentials file not found at %s", creds_path)
            raise FileNotFoundError(
                f"Firebase credentials file not found at {creds_path}. "
                f"Please set FIREBASE_CREDENTIALS_PATH environment variable or place firebase-admin-key.json in the project root."
            )

def get_firestore_client():
    """Get Firestore client instance"""
    init_firebase()
    return firestore.client()

# Initialize on module import
db = None

def get_db():
    """Get or create Firestore database instance"""
    global db
    if db is None:
        db = get_firestore_client()
    return db
