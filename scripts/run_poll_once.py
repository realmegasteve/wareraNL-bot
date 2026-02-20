#!/usr/bin/env python3
"""Run a single poll cycle against the API and show DB results.

Usage: activate venv and run:
  python scripts/run_poll_once.py

This script reads `config.json` and `_api_keys.json` for settings.
It will NOT start the Discord bot; it's for local testing only.
"""
import argparse
import asyncio
import json
import os
from pprint import pprint
import logging

from services.api_client import APIClient
from services.db import Database

logger = logging.getLogger("discord_bot")

async def main():
    cfg = {}
    if os.path.exists("config.json"):
        with open("config.json") as f:
            cfg = json.load(f)

    base_url = cfg.get("api_base_url") or "https://api.example.local/trpc"

    api_keys = None
    if os.path.exists("_api_keys.json"):
        try:
            with open("_api_keys.json") as kf:
                api_keys = json.load(kf).get("keys", [])
        except Exception:
            pass

    client = APIClient(base_url=base_url, api_keys=api_keys)
    await client.start()

    db_path = cfg.get("external_db_path", "database/external.db")
    db = Database(db_path)
    await db.setup()

    parser = argparse.ArgumentParser(description="Run a single poll cycle and inspect country data")
    parser.add_argument("--country", "-c", help="Country ID to fetch (if omitted, will fetch up to 5 countries)")
    args = parser.parse_args()

    if args.country:
        country_ids = [args.country]
    else:
        logger.debug("Fetching country list from:", base_url)
        try:
            countries = await client.get("/country.getAllCountries")
        except Exception as e:
            logger.debug("Failed to fetch country list:", e)
            await client.close()
            await db.close()
            return

        logger.debug("Country list (raw):")
        logger.debug(countries)
        with open(f"output.txt", "w") as f:
                f.write(f"Countries raw response:\n")
                json.dump(countries, f, indent=2)
                f.write("\n")

        # derive up to 5 country ids and capture ruling party ids
        country_ids = []
        ruling_party_map = {}
        if isinstance(countries, list):
            for item in countries:
                if isinstance(item, (str, int)):
                    country_ids.append(str(item))
                elif isinstance(item, dict):
                    cid = None
                    for key in ("id", "countryId", "country_id"):
                        if key in item:
                            cid = str(item[key])
                            country_ids.append(cid)
                            break
                    for key in ("rulingParty", "ruling_party", "rulingPartyId", "ruling_party_id"):
                        if key in item and cid:
                            rp = item[key]
                            if rp is None:
                                break
                            if isinstance(rp, dict):
                                for k2 in ("id", "partyId", "party_id"):
                                    if k2 in rp and rp[k2] is not None:
                                        ruling_party_map[cid] = str(rp[k2])
                                        break
                            else:
                                if rp:
                                    ruling_party_map[cid] = str(rp)
                            break
        elif isinstance(countries, dict):
            for k in ("countries", "data", "result"):
                v = countries.get(k)
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, (str, int)):
                            country_ids.append(str(item))
                        elif isinstance(item, dict):
                            cid = None
                            for key in ("id", "countryId", "country_id"):
                                if key in item:
                                    cid = str(item[key])
                                    country_ids.append(cid)
                                    break
                            for key in ("rulingParty", "ruling_party", "rulingPartyId", "ruling_party_id"):
                                if key in item and cid:
                                    rp = item[key]
                                    if rp is None:
                                        break
                                    if isinstance(rp, dict):
                                        for k2 in ("id", "partyId", "party_id"):
                                            if k2 in rp and rp[k2] is not None:
                                                ruling_party_map[cid] = str(rp[k2])
                                                break
                                    else:
                                        if rp:
                                            ruling_party_map[cid] = str(rp)
                                    break
                    break

        logger.debug("Derived country ids:", country_ids)

    for cid in country_ids:
        try:
            country = await client.get("/country.getCountryById", params={"input": json.dumps({"countryId": cid})})
            logger.debug(f"\nCountry {cid} raw response:")
            # pprint(country)
            

            # store minimal state
            pb = None
            sr = None
            if isinstance(country, dict):
                for k in ("productionBonus", "production_bonus", "production"):
                    if k in country:
                        pb = country[k]
                        break
                for k in ("specializationResource", "specialization_resource", "specialization"):
                    if k in country:
                        sr = country[k]
                        break
            # apply ruling party "industrialism" ethics if available
            adjusted_pb = None
            ethics_info = None
            rp_id = ruling_party_map.get(cid)
            if rp_id:
                try:
                    party = await client.get("/party.getById", params={"input": json.dumps({"partyId": rp_id})})
                    industrial_level = None
                    # try to find ethics object in common response shapes
                    ethics_obj = None
                    if isinstance(party, dict):
                        # common shapes: {"result": {"data": {"ethics": {...}}}}, or {"data": {"ethics": {...}}}
                        obj = party
                        obj = obj.get("result") if isinstance(obj, dict) else None
                        obj = obj.get("data") if isinstance(obj, dict) else None
                        if isinstance(obj, dict) and "ethics" in obj:
                            ethics_obj = obj.get("ethics")
                        else:
                            # fallback: direct data.ethics or party.get("data")
                            obj2 = party.get("data") if isinstance(party, dict) else None
                            if isinstance(obj2, dict) and "ethics" in obj2:
                                ethics_obj = obj2.get("ethics")
                            elif "ethics" in party:
                                ethics_obj = party.get("ethics")
                    if isinstance(ethics_obj, dict):
                        industrial_level = ethics_obj.get("industrialism")
                        if industrial_level is None:
                            industrial_level = ethics_obj.get("industrial")
                        ethics_info = ethics_obj
                    # industrialism adds percentage points (e.g. +30 means 33 -> 63)
                    extra_points = 0.0
                    if industrial_level == 1:
                        extra_points = 10.0
                    elif industrial_level == 2:
                        extra_points = 30.0
                    # compute adjusted production bonus if base pb is numeric
                    if pb is not None:
                        try:
                            pb_num = float(pb)
                            adjusted_pb = pb_num + extra_points
                        except Exception:
                            adjusted_pb = None
                    logger.debug(f"  Ruling party {rp_id} industrialism={industrial_level} -> bonus +{int(extra_points)}pp")
                except Exception as e:
                    logger.debug(f"  Failed to fetch party {rp_id}: {e}")

            state = {
                "production_bonus": pb,
                "production_bonus_with_ethics": adjusted_pb,
                "specialization": sr,
                "ethics": ethics_info,
            }
            await db.set_poll_state(f"country:{cid}:state", json.dumps(state, default=str))
            logger.debug("Persisted state:")
            # logger.debug(await db.get_poll_state(f"country:{cid}:state"))
        except Exception as e:
            logger.debug(f"Failed to fetch/store country {cid}: {e}")

    await client.close()
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
