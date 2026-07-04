"""Backfills the real OnBuy-provided fields (OPC, confirmed active status)
for products whose create_product call only returned a queue_id.

A queue_id means "accepted for async processing" - not created, not
approved. Per OnBuy support (2026-07-02), GET /v2/queues shows the real
outcome once it's processed. The main pipeline (generate_xml.py) doesn't
wait for that - it would add unpredictable delay to every run - so this runs
separately, the same way fetch_listing_ids.py already backfills Listing ID.

NOTE: this is a first version against a real but only lightly-tested OnBuy
endpoint - the queue_id filter on GET /v2/queues didn't actually filter
anything in testing (every value returned the same recent history), so this
instead pages through recent submissions and matches by "uid" (the SKU).
Check the printed output the first few times you run this to confirm it's
finding what you expect; the pagination behavior may need adjusting once
seen at real scale.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import supabase_db
from generate_xml import col_letter
from onbuy_client import OnBuyClient

MAX_PAGES = int(os.getenv("BACKFILL_MAX_PAGES") or "20")
PAGE_SIZE = 50

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open("OnBuy_Feed_Master").sheet1

headers = sheet.row_values(1)
col_map = {col: idx + 1 for idx, col in enumerate(headers)}
data = sheet.get_all_records()

if "Sync Status" not in col_map:
    print("Sheet has no 'Sync Status' column - nothing to check.")
    raise SystemExit(0)

pending = {}  # sku -> sheet row index
for idx, row in enumerate(data):
    sku = str(row.get("SKU") or "").strip()
    status = str(row.get("Sync Status") or "").strip()
    if sku and status == "Pending Approval":
        pending[sku] = idx + 2

if not pending:
    print("No rows with Sync Status = 'Pending Approval' found - nothing to check.")
    raise SystemExit(0)

print(f"Checking {len(pending)} pending SKU(s) against OnBuy's queue history...")

use_sandbox = os.getenv("ONBUY_USE_SANDBOX", "false").strip().lower() == "true"
onbuy = OnBuyClient(use_sandbox=use_sandbox)
if not onbuy.authenticate():
    print("FAILED to authenticate with OnBuy")
    raise SystemExit(1)

found = {}
offset = 0
for _ in range(MAX_PAGES):
    if len(found) >= len(pending):
        break
    try:
        result = onbuy.list_queue(limit=PAGE_SIZE, offset=offset)
    except Exception as exc:
        print(f"Queue lookup failed at offset {offset}: {exc}")
        break

    entries = result.get("results", []) if isinstance(result, dict) else []
    if not entries:
        break

    for entry in entries:
        uid = str(entry.get("uid", "")).strip()
        if uid in pending and uid not in found:
            found[uid] = entry

    offset += PAGE_SIZE

print(f"Found {len(found)} of {len(pending)} pending SKU(s) in the queue history.")

sheet_updates = []
supabase_rows = []

for sku, entry in found.items():
    row_index = pending[sku]
    status = entry.get("status")

    if status == "success":
        opc = entry.get("opc", "")
        sync_status = "Synced"
        listing_active = "TRUE"
    else:
        opc = None
        sync_status = f"Failed: {entry.get('error_message', 'unknown error')}"
        listing_active = "FALSE"

    print(f"{sku}: status={status}, opc={opc}")

    if opc and "OPC" in col_map:
        sheet_updates.append({"range": f"{col_letter(col_map['OPC'])}{row_index}", "values": [[opc]]})
    if "Sync Status" in col_map:
        sheet_updates.append({"range": f"{col_letter(col_map['Sync Status'])}{row_index}", "values": [[sync_status]]})
    if "OnBuy Listing Active" in col_map:
        sheet_updates.append({"range": f"{col_letter(col_map['OnBuy Listing Active'])}{row_index}", "values": [[listing_active]]})

    supabase_row = {"SKU": sku, "Sync Status": sync_status, "OnBuy Listing Active": listing_active}
    if opc:
        supabase_row["OPC"] = opc
    supabase_rows.append(supabase_row)

if sheet_updates:
    sheet.batch_update(sheet_updates)
    print(f"Updated {len(sheet_updates)} sheet cell(s).")

if supabase_rows:
    supabase_db.upsert_products(supabase_rows)
    print(f"Upserted {len(supabase_rows)} Supabase row(s).")

still_pending = set(pending) - set(found)
if still_pending:
    print(f"Still pending (not found in queue history yet): {', '.join(still_pending)}")
