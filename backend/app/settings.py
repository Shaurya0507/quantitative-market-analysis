import os
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required; copy .env.example to .env and configure it.")
    return database_url


def get_market_api_key() -> str:
    api_key = os.getenv("MARKET_API_KEY")
    if not api_key or api_key == "replace-with-your-alpha-vantage-api-key":
        raise RuntimeError("Set MARKET_API_KEY in .env before running ingestion.")
    return api_key


def get_supported_stocks() -> dict[str, dict[str, Any]]:
    metadata_path = Path(__file__).with_name("supported_stocks.json")
    try:
        stocks = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load supported ticker catalog at {metadata_path}") from exc
    return {stock["ticker"]: stock for stock in stocks}
