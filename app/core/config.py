import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "gateway.db")
ADMIN_PIN = os.getenv("ADMIN_PIN", "9876")
MAX_BOOKS = int(os.getenv("MAX_BOOKS", "5"))
MAX_DAYS = int(os.getenv("MAX_DAYS", "14"))
CARDCODE_PREFIX = os.getenv("CARDCODE_PREFIX", "21000000")
HEARTBEAT_SECONDS = int(os.getenv("APP_HEARTBEAT_SECONDS", "1800"))

EXPECTED_ACTIVATION_KEY = "AB2025-ELIBRA-MIDDLEWARE-AIDAR-BEGOTAYEV"
EXPECTED_ACTIVATION_PASSWORD = "AB2025-PROJECT"

APP_ACTIVATION_KEY = os.getenv("APP_ACTIVATION_KEY", "")
APP_ACTIVATION_PASSWORD = os.getenv("APP_ACTIVATION_PASSWORD", "")

DISCORD_STARTUP_WEBHOOK_URL = os.getenv("DISCORD_STARTUP_WEBHOOK_URL", "")
DISCORD_EVENTS_WEBHOOK_URL = os.getenv("DISCORD_EVENTS_WEBHOOK_URL", "") or DISCORD_STARTUP_WEBHOOK_URL

# SMTP — email notifications (due date reminders)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "library@coventry.edu.kz")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

# Test email for overdue notifier test mode
TEST_EMAIL = os.getenv("TEST_EMAIL", "a.begotayev@coventry.edu.kz")
