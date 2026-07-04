"""Upserts processed product rows into the Supabase Postgres table.

Distinct from storage.py (which uploads feed.xml to Supabase Storage) - this
writes to the actual "OnBuy_Feed_Master" table via PostgREST, using the
upsert-via-POST pattern (Prefer: resolution=merge-duplicates) keyed on SKU,
the table's primary key. This mirrors what gets written to the Google Sheet
so Supabase accumulates a queryable copy of the catalog without yet becoming
the pipeline's source of truth.
"""
import logging
import os

import requests

logger = logging.getLogger("onbuy_sync")

TABLE_NAME = "OnBuy_Feed_Master"  # case-sensitive - matches the quoted identifier in the table DDL


def upsert_products(rows):
    """rows: list of dicts using the exact column names from the Supabase
    table (including spaces/case/currency symbols, e.g. "Cost Price (£)").
    Returns True on success. Never raises - a failed Supabase export must not
    fail the whole run, since the Sheet + OnBuy updates already happened by
    the time this is called.
    """
    if not rows:
        return True

    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not service_key:
        logger.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY not set - skipping database export")
        return False

    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/{TABLE_NAME}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=rows, timeout=30)
    except requests.exceptions.RequestException as exc:
        logger.error("Supabase database export failed: %s", exc)
        return False

    if resp.status_code not in (200, 201, 204):
        logger.error("Supabase database export failed (%s): %s", resp.status_code, resp.text[:500])
        return False

    logger.info("Supabase database export: upserted %d row(s)", len(rows))
    return True


def fetch_existing_opcs(skus):
    """Returns {sku: opc} for whichever of these SKUs already have a Supabase
    row. Used so the regular sync run can avoid clobbering a real OPC (set by
    backfill_onbuy_status.py once OnBuy's approval queue clears) back to the
    "PENDING" placeholder every time it runs. Never raises - returns {} on
    any failure, which just means every row falls back to "PENDING" for OPC
    this run (safe, if not perfectly up to date).
    """
    if not skus:
        return {}

    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY")
    if not supabase_url or not service_key:
        return {}

    endpoint = f"{supabase_url.rstrip('/')}/rest/v1/{TABLE_NAME}"
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    params = {"select": "SKU,OPC", "SKU": f"in.({','.join(skus)})"}

    try:
        resp = requests.get(endpoint, headers=headers, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        logger.error("Fetching existing OPCs failed: %s", exc)
        return {}

    if resp.status_code != 200:
        logger.error("Fetching existing OPCs failed (%s): %s", resp.status_code, resp.text[:300])
        return {}

    try:
        return {row["SKU"]: row.get("OPC") for row in resp.json()}
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("Fetching existing OPCs: unexpected response shape: %s", exc)
        return {}
