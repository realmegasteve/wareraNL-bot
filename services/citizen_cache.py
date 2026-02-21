"""Handles fetching and caching citizen level data from the WarEra API."""

import json
import logging
from datetime import datetime
from typing import Any

from services.api_client import APIClient
from services.db import Database

logger = logging.getLogger("discord_bot")


class CitizenCache:
    """Fetches citizen level data from the API and persists it to the DB cache."""

    def __init__(self, client: APIClient, db: Database) -> None:
        self._client = client
        self._db = db

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    async def refresh_country(self, country_id: str, country_name: str, *, progress_msg=None) -> int:
        """Fetch every citizen's level for a country and write to DB cache.

        Uses tRPC HTTP batching (30 users per request) with automatic fallback
        to individual calls if the server doesn't support batching.

        Returns the number of citizens whose level was successfully recorded.
        """
        user_ids = await self._fetch_user_ids(country_id)
        if not user_ids:
            return 0

        await self._db.delete_citizens_for_country(country_id)

        updated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        batch_size = 30
        inputs = [{"userId": uid} for uid in user_ids]
        total_batches = (len(user_ids) + batch_size - 1) // batch_size

        results = await self._client.batch_get(
            "/user.getUserLite",
            inputs,
            batch_size=batch_size,
            chunk_sleep=1.0,
        )

        recorded = 0
        for i, (uid, obj) in enumerate(zip(user_ids, results)):
            lvl = self._extract_level(obj)
            if lvl is not None:
                await self._db.upsert_citizen_level(uid, country_id, lvl, updated_at)
                recorded += 1

            if (i + 1) % (batch_size * 2) == 0:
                await self._db.flush_citizen_levels()
                if progress_msg:
                    batch_done = (i + 1) // batch_size
                    try:
                        await progress_msg.edit(
                            content=(
                                f"Refreshing **{country_name}**: "
                                f"batch {batch_done}/{total_batches} "
                                f"({i + 1}/{len(user_ids)} citizens)…"
                            )
                        )
                    except Exception:
                        pass

        await self._db.flush_citizen_levels()
        return recorded

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    async def _fetch_user_ids(self, country_id: str) -> list[str]:
        """Paginate /user.getUsersByCountry and return all user IDs."""
        user_ids: list[str] = []
        cursor = None
        while True:
            params: dict = {"countryId": country_id, "limit": 100}
            if cursor:
                params["cursor"] = cursor

            resp = await self._client.get(
                "/user.getUsersByCountry",
                params={"input": json.dumps(params)},
            )

            data_obj = resp
            if isinstance(resp, dict):
                for key in ("result", "data"):
                    v = resp.get(key)
                    if isinstance(v, dict):
                        data_obj = v.get("data", v)
                        break

            users: list = []
            next_cursor = None
            if isinstance(data_obj, list):
                users = data_obj
            elif isinstance(data_obj, dict):
                for key in ("items", "users", "data", "result"):
                    v = data_obj.get(key)
                    if isinstance(v, list):
                        users = v
                        break
                next_cursor = (
                    data_obj.get("nextCursor")
                    or data_obj.get("cursor")
                    or data_obj.get("next")
                )

            for user in users:
                if isinstance(user, dict):
                    uid = user.get("_id") or user.get("id") or user.get("userId")
                    if uid:
                        user_ids.append(str(uid))

            if not next_cursor or not users:
                break
            cursor = next_cursor
        return user_ids

    @staticmethod
    def _extract_level(obj: Any) -> int | None:
        """Pull the level integer out of a getUserLite result dict."""
        if not isinstance(obj, dict):
            return None
        try:
            return int(obj["leveling"]["level"])
        except (KeyError, TypeError, ValueError):
            pass
        if obj.get("level") is not None:
            try:
                return int(obj["level"])
            except (ValueError, TypeError):
                pass
        try:
            return int(obj["rankings"]["userLevel"]["value"])
        except (KeyError, TypeError, ValueError):
            pass
        return None
