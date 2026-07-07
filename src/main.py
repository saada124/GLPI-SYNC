#!/usr/bin/env python3
"""GLPI ⇄ AppSheet bidirectional sync orchestrator."""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from glpi_api import GLPIAPI
from sheets_client import SheetsClient
from field_mappings import load_mappings
from cache import StateCache
from sync import Syncer
from logger import setup_logger


def main() -> int:
    load_dotenv()

    logger = setup_logger(level=os.getenv("LOG_LEVEL", "INFO"))

    glpi_url = os.getenv("GLPI_URL")
    glpi_app_token = os.getenv("GLPI_APP_TOKEN")
    glpi_user_token = os.getenv("GLPI_USER_TOKEN")
    sheet_creds = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.getenv("SHEET_ID")

    missing = []
    if not glpi_url:
        missing.append("GLPI_URL")
    if not glpi_app_token:
        missing.append("GLPI_APP_TOKEN")
    if not glpi_user_token:
        missing.append("GLPI_USER_TOKEN")
    if not sheet_creds:
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheet_id:
        missing.append("SHEET_ID")

    if missing:
        logger.error(f"Missing required env vars: {', '.join(missing)}")
        logger.error("Copy .env.example to .env and fill in your credentials.")
        return 1

    creds_path = Path(sheet_creds)
    if not creds_path.exists():
        logger.error(f"Service account JSON not found at: {creds_path}")
        return 1

    logger.info("Loading field mappings...")
    mappings = load_mappings()
    logger.info(f"Loaded {len(mappings)} entity mappings: {list(mappings.keys())}")

    logger.info("Connecting to Google Sheets...")
    try:
        sheets = SheetsClient(str(creds_path), sheet_id)
    except Exception as e:
        logger.error(f"Failed to connect to Google Sheets: {e}")
        return 1

    logger.info("Connecting to GLPI API...")
    try:
        glpi = GLPIAPI(glpi_url, glpi_app_token, glpi_user_token)
    except Exception as e:
        logger.error(f"Failed to create GLPI client: {e}")
        return 1

    cache = StateCache()
    syncer = Syncer(glpi, sheets, mappings, cache)

    interval_min = int(os.getenv("SYNC_INTERVAL_MINUTES", "10"))
    run_once = "--once" in sys.argv

    if run_once:
        with glpi:
            errors = syncer.run()
        return 1 if errors else 0

    logger.info(f"Entering polling loop (every {interval_min} min)...")

    while True:
        try:
            with glpi:
                syncer.run()
        except KeyboardInterrupt:
            logger.info("Shutting down (Ctrl+C)...")
            break
        except Exception as e:
            logger.exception(f"Unhandled error in sync cycle: {e}")

        logger.info(f"Sleeping for {interval_min} minutes...")
        time.sleep(interval_min * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
