import os
import re
import email
import sqlite3
from email import policy
from datetime import datetime
from tools.secrets import get_secret
from tools.storage import s3, BUCKET

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ledger.db")
ETRANSFER_PREFIX = "etransfer-emails/"


def _get_rent_config():
    return get_secret("personal-ai/rent-config")


def _resolve_property(sender_name: str, rent_config: dict):
    """Returns (property, unit_type) where unit_type is 'main' or 'second'."""
    name_lower = sender_name.lower().strip()
    for prop, cfg in rent_config.items():
        for tenant in cfg.get("tenants_main", []):
            if tenant.lower() in name_lower or name_lower in tenant.lower():
                return (prop, "main")
        for tenant in cfg.get("tenants_second", []):
            if tenant.lower() in name_lower or name_lower in tenant.lower():
                return (prop, "second")
    return ("", "")


def init_ledger():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            property TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'main',
            type TEXT NOT NULL,
            sender TEXT,
            amount REAL NOT NULL,
            description TEXT,
            s3_key TEXT UNIQUE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate: add unit column if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "unit" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN unit TEXT NOT NULL DEFAULT 'main'")
    conn.commit()
    conn.close()


def _migrate_unit_labels():
    """One-time migration: set unit='second' for known second-unit tenants."""
    rent_config = _get_rent_config()
    conn = sqlite3.connect(DB_PATH)
    for prop, cfg in rent_config.items():
        for tenant in cfg.get("tenants_second", []):
            conn.execute(
                "UPDATE transactions SET unit = 'second' WHERE property = ? AND sender = ? AND unit = 'main'",
                (prop, tenant.lower()),
            )
    conn.commit()
    conn.close()


init_ledger()
_migrate_unit_labels()


def _parse_etransfer_email(raw_bytes: bytes):
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    subject = msg["Subject"] or ""

    # Pattern 1: "You've received $X from NAME and it has been automatically deposited"
    m = re.search(r"\$([\d,]+\.\d{2}) from (.+?)(?:\s+and)", subject)
    if not m:
        # Pattern 2: "NAME sent you $X"
        m = re.search(r"Transfer: (.+?) sent you \$([\d,]+\.\d{2})", subject)
        if m:
            sender = m.group(1).strip()
            amount = float(m.group(2).replace(",", ""))
        else:
            return None
    else:
        amount = float(m.group(1).replace(",", ""))
        sender = m.group(2).strip()

    # Get original transfer date from forwarded body
    transfer_date = None
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
    else:
        body = msg.get_content()

    date_match = re.search(r"Date: .+?, (.+?\d{4})", body)
    if date_match:
        date_str = date_match.group(1).strip()
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y"):
            try:
                transfer_date = datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue

    if not transfer_date:
        # Fall back to email Date header
        msg_date = msg["Date"]
        if msg_date:
            from email.utils import parsedate_to_datetime
            transfer_date = parsedate_to_datetime(str(msg_date)).strftime("%Y-%m-%d")

    return {
        "amount": amount,
        "sender": sender,
        "date": transfer_date or datetime.now().strftime("%Y-%m-%d"),
    }


def _dedup_key(parsed: dict) -> str:
    """Unique key for a payment: sender+amount+date. Prevents duplicates from multiple forwards."""
    return f"{parsed['sender'].lower()}|{parsed['amount']}|{parsed['date']}"


def ingest_etransfers() -> list:
    """Scan S3 for new e-transfer emails, add to ledger, then delete from S3."""
    rent_config = _get_rent_config()

    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=ETRANSFER_PREFIX)
    objects = [obj for obj in resp.get("Contents", []) if "SETUP" not in obj["Key"]]

    if not objects:
        return []

    conn = sqlite3.connect(DB_PATH)
    new_transactions = []
    keys_to_delete = []

    for obj in objects:
        key = obj["Key"]
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        parsed = _parse_etransfer_email(raw)

        if not parsed:
            keys_to_delete.append(key)
            continue

        # Dedup: skip if same sender+amount+date already in ledger
        dedup = _dedup_key(parsed)
        existing = conn.execute(
            "SELECT id FROM transactions WHERE sender = ? AND amount = ? AND date = ?",
            (parsed["sender"].lower(), parsed["amount"], parsed["date"]),
        ).fetchone()

        if existing:
            keys_to_delete.append(key)
            continue

        prop, unit = _resolve_property(parsed["sender"], rent_config)
        if not prop:
            description = f"Unknown tenant: {parsed['sender']}"
            unit = "main"
        else:
            description = f"Rent payment from {parsed['sender']}"

        conn.execute(
            "INSERT INTO transactions (date, property, unit, type, sender, amount, description, s3_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (parsed["date"], prop, unit, "rent", parsed["sender"].lower(), parsed["amount"], description, key),
        )
        new_transactions.append({
            "date": parsed["date"],
            "property": prop,
            "sender": parsed["sender"],
            "amount": parsed["amount"],
        })
        keys_to_delete.append(key)

    conn.commit()
    conn.close()

    # Delete processed emails from S3
    for key in keys_to_delete:
        s3.delete_object(Bucket=BUCKET, Key=key)

    return new_transactions


def get_balance(month: str = None) -> dict:
    """Get rent balance per property/unit for a given month (YYYY-MM) or all time."""
    rent_config = _get_rent_config()
    conn = sqlite3.connect(DB_PATH)

    results = {}
    for prop, cfg in rent_config.items():
        prop_result = {}
        for unit, expected in [("main", cfg["monthly_rent"]), ("second", cfg["second_unit"])]:
            if expected == 0:
                continue
            if month:
                row = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE property = ? AND unit = ? AND type = 'rent' AND date LIKE ?",
                    (prop, unit, f"{month}%"),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE property = ? AND unit = ? AND type = 'rent'",
                    (prop, unit),
                ).fetchone()

            received = row[0]
            entry = {"expected": expected, "received": received}
            if month:
                entry["outstanding"] = round(expected - received, 2)
            prop_result[unit] = entry

        results[prop] = prop_result

    conn.close()
    return results


def get_transactions(property_slug: str = "", month: str = "") -> list:
    """Get transactions with optional filters."""
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT date, property, unit, type, sender, amount, description FROM transactions WHERE 1=1"
    params = []

    if property_slug:
        query += " AND property = ?"
        params.append(property_slug)
    if month:
        query += " AND date LIKE ?"
        params.append(f"{month}%")

    query += " ORDER BY date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    return [
        {"date": r[0], "property": r[1], "unit": r[2], "type": r[3], "sender": r[4], "amount": r[5], "description": r[6]}
        for r in rows
    ]


def get_monthly_summary(month: str) -> dict:
    """Full P&L for a month: rent in, utilities receivable, net per property.

    Utilities are attributed to the month they're DUE (payment month),
    not the usage month. The tenant utility bill is sent on the 1st and
    only applies to the main tenant (not second units).
    Internet is a fixed monthly charge for windmill main only.
    """
    from tools.billing import split_by_usage_month

    rent_config = _get_rent_config()
    balance = get_balance(month)

    # Previous month's usage = this month's receivable
    year, mo = int(month[:4]), int(month[5:7])
    prev_mo = mo - 1
    prev_year = year
    if prev_mo == 0:
        prev_mo = 12
        prev_year -= 1
    prev_usage_month = f"{prev_year:04d}-{prev_mo:02d}"

    bills = split_by_usage_month(prev_usage_month)

    summary = {}
    for prop, cfg in rent_config.items():
        prop_balance = balance.get(prop, {})

        # Main unit
        main_bal = prop_balance.get("main", {})
        main_expected = main_bal.get("expected", cfg["monthly_rent"])
        main_received = main_bal.get("received", 0)

        # Second unit
        second_bal = prop_balance.get("second", {})
        second_expected = second_bal.get("expected", cfg.get("second_unit", 0))
        second_received = second_bal.get("received", 0)

        # Utility costs (main tenant only)
        landlord_expenses = 0
        tenant_receivable = 0
        if bills.success:
            for b in bills.data["bills"]:
                if b["property"] == prop:
                    landlord_expenses += b["landlord_amount"]
                    tenant_receivable += b["tenant_amount"]

        # Fixed internet charge (main tenant only)
        internet = cfg.get("internet", 0)
        tenant_receivable += internet

        prop_summary = {
            "main": {
                "rent_expected": main_expected,
                "rent_received": main_received,
                "rent_outstanding": round(main_expected - main_received, 2),
                "utility_receivable": round(tenant_receivable, 2),
                "internet_included": internet,
                "total_receivable": round(main_expected - main_received + tenant_receivable, 2),
            },
            "utility_expenses_landlord": round(landlord_expenses, 2),
        }

        if second_expected > 0:
            prop_summary["second"] = {
                "rent_expected": second_expected,
                "rent_received": second_received,
                "rent_outstanding": round(second_expected - second_received, 2),
            }

        total_received = main_received + second_received
        total_expected = main_expected + second_expected
        prop_summary["totals"] = {
            "rent_expected": total_expected,
            "rent_received": total_received,
            "rent_outstanding": round(total_expected - total_received, 2),
            "tenant_utility_receivable": round(tenant_receivable, 2),
            "net_income": round(total_received + tenant_receivable - landlord_expenses, 2),
        }

        summary[prop] = prop_summary

    return summary
