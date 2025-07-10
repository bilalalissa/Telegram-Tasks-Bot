import os
from dotenv import load_dotenv

# load environment from .env
load_dotenv()

TELEGRAM_TOKEN      = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY")
ANOTHER_SERVICE_KEY = os.getenv("ANOTHER_SERVICE_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN in .env")
