#!/usr/bin/env python3
"""Fetch Octopus Agile import and Agile Outgoing export rates for London (region C).

Writes to:
  - data/agile.duckdb  (tariff_prices table, for offline backtest)
  - Supabase tariff_prices table via psycopg (upsert, for live sim)

Resolves the current Agile product dynamically — no hard-coded versions.
Pulls approximately six months of 30-minute slots for both directions.
"""
import os, sys, datetime, logging
from pathlib import Path

import time

import httpx
import duckdb
import psycopg
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

OCTOPUS_BASE = "https://api.octopus.energy/v1"
REGION = "C"
PAGE_SIZE = 500
MONTHS_BACK = 6
DUCKDB_PATH = Path(__file__).parent.parent / "data" / "agile.duckdb"


def _get(url: str, params: dict | None = None, retries: int = 5) -> dict:
    """GET with exponential backoff on 429."""
    for attempt in range(retries):
        resp = httpx.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt * 5
            log.warning("429 rate-limited — waiting %ds (attempt %d/%d)", wait, attempt + 1, retries)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Exhausted retries for {url}")


def _resolve_tariff_codes() -> tuple[str, str, str, str]:
    """Return (import_product, import_tariff, export_product, export_tariff) for region C."""
    products = _get(f"{OCTOPUS_BASE}/products/", {"is_prepay": False, "is_business": False})["results"]

    import_product = next(
        p["code"] for p in products
        if p["code"].startswith("AGILE-") and "OUTGOING" not in p["code"]
    )
    export_product = next(
        p["code"] for p in products
        if "AGILE-OUTGOING" in p["code"]
    )

    def tariff_code(product: str) -> str:
        data = _get(f"{OCTOPUS_BASE}/products/{product}/")
        tariffs = data["single_register_electricity_tariffs"]
        return tariffs[f"_{REGION}"]["direct_debit_monthly"]["code"]

    return import_product, tariff_code(import_product), export_product, tariff_code(export_product)


def _fetch_rates(product: str, tariff: str, period_from: str, period_to: str) -> list[dict]:
    """Paginate through all half-hourly rates for the given tariff and date window.

    Uses explicit page numbers so period_from/period_to are always sent —
    the API's `next` links drop those params, which would return unbounded history.
    """
    base_url = f"{OCTOPUS_BASE}/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/"
    rows = []
    page = 1
    while True:
        params = {
            "period_from": period_from,
            "period_to": period_to,
            "page_size": PAGE_SIZE,
            "page": page,
        }
        data = _get(base_url, params)
        batch = data["results"]
        rows.extend(batch)
        log.info("  page %d — %d rows so far", page, len(rows))
        if not data.get("next") or not batch:
            break
        page += 1
        time.sleep(0.2)
    return rows


def _to_records(rates: list[dict], direction: str) -> list[tuple]:
    """Convert raw API results to (region, direction, slot_start, price_p_per_kwh) tuples."""
    out = []
    for r in rates:
        if r.get("valid_from") is None:
            continue
        out.append((REGION, direction, r["valid_from"], float(r["value_inc_vat"])))
    return out


def _write_duckdb(import_records: list[tuple], export_records: list[tuple]) -> None:
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS tariff_prices (
            region             TEXT NOT NULL,
            direction          TEXT NOT NULL,
            slot_start         TIMESTAMPTZ NOT NULL,
            price_p_per_kwh    DOUBLE NOT NULL,
            PRIMARY KEY (region, direction, slot_start)
        )
    """)
    all_records = import_records + export_records
    con.executemany(
        "INSERT OR REPLACE INTO tariff_prices VALUES (?, ?, ?, ?)",
        all_records,
    )
    count = con.execute("SELECT count(*) FROM tariff_prices").fetchone()[0]
    con.close()
    log.info("DuckDB: %d rows in tariff_prices", count)


def _upsert_supabase(records: list[tuple]) -> None:
    db_url = os.environ["DATABASE_URL"]
    sql = """
        CREATE TABLE IF NOT EXISTS tariff_prices (
            id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            region          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            slot_start      TIMESTAMPTZ NOT NULL,
            price_p_per_kwh NUMERIC NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (region, direction, slot_start)
        )
    """
    upsert = """
        INSERT INTO tariff_prices (region, direction, slot_start, price_p_per_kwh)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (region, direction, slot_start)
        DO UPDATE SET price_p_per_kwh = EXCLUDED.price_p_per_kwh
    """
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            conn.commit()
            batch_size = 500
            for i in range(0, len(records), batch_size):
                cur.executemany(upsert, records[i : i + batch_size])
            conn.commit()
    log.info("Supabase: upserted %d rows into tariff_prices", len(records))


def main() -> None:
    log.info("Resolving current Agile product codes...")
    import_product, import_tariff, export_product, export_tariff = _resolve_tariff_codes()
    log.info("Import: %s  |  %s", import_product, import_tariff)
    log.info("Export: %s  |  %s", export_product, export_tariff)

    now = datetime.datetime.now(datetime.timezone.utc)
    period_from = (now - datetime.timedelta(days=MONTHS_BACK * 30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    period_to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("Fetching rates from %s to %s", period_from, period_to)

    log.info("Fetching import rates...")
    import_rates = _fetch_rates(import_product, import_tariff, period_from, period_to)
    log.info("Import: %d slots fetched", len(import_rates))

    log.info("Fetching export rates...")
    export_rates = _fetch_rates(export_product, export_tariff, period_from, period_to)
    log.info("Export: %d slots fetched", len(export_rates))

    import_records = _to_records(import_rates, "import")
    export_records = _to_records(export_rates, "export")

    log.info("Writing to DuckDB (%s)...", DUCKDB_PATH)
    _write_duckdb(import_records, export_records)

    log.info("Upserting into Supabase...")
    _upsert_supabase(import_records + export_records)

    log.info("Done. %d import + %d export = %d total slots.",
             len(import_records), len(export_records), len(import_records) + len(export_records))


if __name__ == "__main__":
    main()
