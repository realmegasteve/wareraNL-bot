#!/usr/bin/env python3
"""Test script to simulate a specialization-top change.

Usage examples:
  # set a fake previous top then run poll detection for cookedFish
  python -m scripts.test_specialization_change --item cookedFish --set-prev-country-id fakeid --set-prev-country-name Oldland --set-prev-bonus 10

This script updates the `specialization_top` table (if requested), then fetches
`/country.getAllCountries`, computes tops the same way the poller does, compares
with DB, prints detected changes, and persists the new tops (unless --no-persist).
"""
import argparse
import asyncio
import json
import os
from datetime import datetime
from pprint import pprint

from services.api_client import APIClient
from services.db import Database


async def compute_tops(client: APIClient):
    all_countries = await client.get("/country.getAllCountries")
    country_list = []
    if isinstance(all_countries, list):
        country_list = [c for c in all_countries if isinstance(c, dict)]
    elif isinstance(all_countries, dict):
        if isinstance(all_countries.get("data"), list):
            country_list = [c for c in all_countries.get("data") if isinstance(c, dict)]
        elif isinstance(all_countries.get("result"), dict) and isinstance(all_countries.get("result").get("data"), list):
            country_list = [c for c in all_countries.get("result").get("data") if isinstance(c, dict)]
        else:
            for key in ("countries", "data", "result", "items"):
                v = all_countries.get(key)
                if isinstance(v, list):
                    country_list = [c for c in v if isinstance(c, dict)]
                    break

    def _get_production_bonus(obj):
        try:
            rb = obj.get("rankings", {}).get("countryProductionBonus")
            if isinstance(rb, dict) and "value" in rb:
                return float(rb.get("value"))
        except Exception:
            pass
        try:
            sp = obj.get("strategicResources", {}).get("bonuses", {}).get("productionPercent")
            if sp is not None:
                return float(sp)
        except Exception:
            pass
        return None

    tops = {}
    for country in country_list:
        cid = country.get("_id") or country.get("id") or country.get("countryId") or country.get("code")
        name = country.get("name")
        item = country.get("specializedItem") or country.get("specialized_item") or country.get("specialization")
        pb = _get_production_bonus(country)
        if not item or not cid:
            continue
        cur = tops.get(item)
        cur_val = cur.get("production_bonus") if cur else None
        cur_val = float(cur_val) if cur_val is not None else float("-inf")
        this_val = float(pb) if pb is not None else float("-inf")
        if this_val > cur_val:
            tops[item] = {"country_id": str(cid), "country_name": name, "production_bonus": pb}

    return tops


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--item", "-i", help="Specialization item to test (default: cookedFish)", default="cookedFish")
    parser.add_argument("--set-prev-country-id", help="If provided, set this country_id as previous top before running")
    parser.add_argument("--set-prev-country-name", help="Name for the fake previous top")
    parser.add_argument("--set-prev-bonus", type=float, help="Bonus value for the fake previous top")
    parser.add_argument("--no-persist", action="store_true", help="Do not persist new tops to DB")
    args = parser.parse_args()

    # load config and api key
    cfg = {}
    if os.path.exists("config.json"):
        with open("config.json") as f:
            cfg = json.load(f)
    base_url = cfg.get("api_base_url", "https://api.example.local/trpc")
    api_key = None
    if os.path.exists("_api_keys.json"):
        try:
            api_key = json.load(open("_api_keys.json")).get("keys", [None])[0]
        except Exception:
            api_key = None

    headers = {"x-api-key": api_key} if api_key else None
    client = APIClient(base_url=base_url, headers=headers)
    await client.start()

    db_path = cfg.get("external_db_path", "database/external.db")
    db = Database(db_path)
    await db.setup()

    item = args.item

    # optionally set fake previous top
    if args.set_prev_country_id:
        now = datetime.utcnow().isoformat() + "Z"
        prev_name = args.set_prev_country_name or "(fake)"
        prev_bonus = float(args.set_prev_bonus) if args.set_prev_bonus is not None else 0.0
        print(f"Setting previous top for '{item}' to {prev_name} ({args.set_prev_country_id}) bonus={prev_bonus}")
        await db.set_top_specialization(item, args.set_prev_country_id, prev_name, prev_bonus, now)

    # show previous
    prev = await db.get_top_specialization(item)
    print("Previous top:")
    pprint(prev)

    # compute current tops from API
    tops = await compute_tops(client)
    current_top = tops.get(item)
    print("Current computed top:")
    pprint(current_top)

    # compare and report
    if prev is None and current_top is None:
        print("No previous and no current top for item", item)
    elif prev is None and current_top is not None:
        print(f"Detected new leader for {item}: {current_top}")
    elif current_top is None:
        print(f"No current top computed for {item}")
    else:
        prev_bonus = float(prev.get("production_bonus") or 0)
        cur_bonus = float(current_top.get("production_bonus") or 0)
        if prev.get("country_id") != current_top.get("country_id") or prev_bonus != cur_bonus:
            print(f"CHANGE detected for {item}:")
            print("  previous:", prev.get("country_name"), prev.get("country_id"), "bonus=", prev.get("production_bonus"))
            print("  current:", current_top.get("country_name"), current_top.get("country_id"), "bonus=", current_top.get("production_bonus"))
        else:
            print("No change detected for", item)

    # persist new top unless no-persist
    if not args.no_persist and current_top is not None:
        now = datetime.utcnow().isoformat() + "Z"
        await db.set_top_specialization(item, current_top.get("country_id"), current_top.get("country_name"), float(current_top.get("production_bonus") or 0), now)
        print("Persisted new top for", item)

    await client.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
