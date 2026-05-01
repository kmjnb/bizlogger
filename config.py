import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])
DATABASE_URL = os.environ["DATABASE_URL"]
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", str(ROOT / "storage/media")))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
