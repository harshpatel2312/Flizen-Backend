import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    """Application configuration settings"""
    
    # Firebase
    FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-admin-key.json")
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "flizen-flight-booking")
    
    # CORS
    CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000,http://localhost:3001").split(",")]
    
    # Environment
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    DEBUG = ENVIRONMENT == "development"
    
    # Duffel API
    DUFFEL_ACCESS_TOKEN = os.getenv("DUFFEL_ACCESS_TOKEN", "")
    DUFFEL_API_BASE_URL = "https://api.duffel.com"
    DUFFEL_API_VERSION = "v2"

    # Stripe
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Frontend
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

settings = Settings()
