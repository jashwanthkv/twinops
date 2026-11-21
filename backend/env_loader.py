"""
env_loader.py — Robust Environment & Configuration Loader for TwinOps Backend.

Ensures backend/.env is automatically loaded into os.environ before database
or message bus connections are initialized.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

_backend_dir = Path(__file__).resolve().parent
_env_file = _backend_dir / ".env"
if _env_file.exists():
    load_dotenv(_env_file, override=False)

def get_mongo_uri() -> str:
    """Return configured MongoDB URI or default to localhost."""
    uri = os.getenv("MONGODBURI", "").strip()
    return uri if uri else "mongodb://localhost:27017"

def get_mongo_database() -> str:
    """Return default MongoDB database name."""
    return os.getenv("MONGODB_DATABASE", "twinops_aie").strip() or "twinops_aie"

def get_redis_url() -> str:
    """Return a valid redis:// connection URL, ignoring invalid schemes."""
    url = os.getenv("REDIS_URL", "").strip()
    if url.startswith("redis://") or url.startswith("rediss://") or url.startswith("unix://"):
        return url
    return "redis://localhost:6379/0"
