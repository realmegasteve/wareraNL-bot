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
        batch_size = 100
        inputs = [{"userId": uid} for uid in user_ids]
        total_batches = (len(user_ids) + batch_size - 1) // batch_size

        results = await self._client.batch_get(
            "/user.getUserLite",
            inputs,
            batch_size=batch_size,
            chunk_sleep=0.5,
        )

        recorded = 0
        for i, (uid, obj) in enumerate(zip(user_ids, results)):
            lvl = self._extract_level(obj)
            mode = self._extract_skill_mode(obj)
            reset_at = self._extract_last_skills_reset_at(obj)
            last_login = self._extract_last_login_at(obj)
            name = self._extract_name(obj)
            if lvl is not None:
                await self._db.upsert_citizen_level(
                    uid, country_id, lvl, updated_at,
                    skill_mode=mode, last_skills_reset_at=reset_at,
                    citizen_name=name, last_login_at=last_login,
                )
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

    @staticmethod
    def _extract_skill_mode(obj: Any) -> str | None:
        """Classify a player as 'eco' or 'war' based on where they spent skill points.

        Points spent in skill at level L = L*(L+1)//2.
        Eco skills : entrepreneurship, energy, production, companies, management.
        War skills : attack, health, hunger, criticalChance, criticalDamages,
                     armor, precision, dodge, lootChance.
        Ties go to eco.
        Returns None when no skill data is available.
        """
        if not isinstance(obj, dict):
            return None
        skills = obj.get("skills")
        if not isinstance(skills, dict):
            return None
        eco_names = {"entrepreneurship", "energy", "production", "companies", "management"}
        war_names = {"attack", "health", "hunger", "criticalChance", "criticalDamages",
                     "armor", "precision", "dodge", "lootChance"}
        eco_pts = 0
        war_pts = 0
        for name, sdata in skills.items():
            if not isinstance(sdata, dict):
                continue
            lv = int(sdata.get("level") or 0)
            pts = lv * (lv + 1) // 2
            if name in eco_names:
                eco_pts += pts
            elif name in war_names:
                war_pts += pts
        return "eco" if eco_pts >= war_pts else "war"

    @staticmethod
    def _extract_name(obj: Any) -> str | None:
        """Pull the player name from a getUserLite result."""
        if not isinstance(obj, dict):
            return None
        for key in ("name", "username", "displayName", "nick"):
            val = obj.get(key)
            if isinstance(val, str) and val:
                return val
        for sub in ("profile", "user"):
            sub_obj = obj.get(sub)
            if isinstance(sub_obj, dict):
                for key in ("name", "username", "displayName"):
                    val = sub_obj.get(key)
                    if isinstance(val, str) and val:
                        return val
        return None

    @staticmethod
    def _extract_last_skills_reset_at(obj: Any) -> str | None:
        """Pull the lastSkillsResetAt ISO timestamp from a getUserLite result.

        The value lives inside the nested ``dates`` object and is absent when
        the player has never reset their skills.
        """
        if not isinstance(obj, dict):
            return None
        dates = obj.get("dates")
        if not isinstance(dates, dict):
            return None
        val = dates.get("lastSkillsResetAt")
        if isinstance(val, str) and val:
            return val
        return None

    @staticmethod
    def _extract_last_login_at(obj: Any) -> str | None:
        """Pull the last-login ISO timestamp from a getUserLite result.

        Tries several candidate field names in the ``dates`` sub-object and at
        the root level, since the exact key varies across API versions.
        """
        if not isinstance(obj, dict):
            return None
        # Try nested ``dates`` object first (same location as lastSkillsResetAt)
        dates = obj.get("dates")
        if isinstance(dates, dict):
            for key in ("lastLoginAt", "lastSeenAt", "lastOnlineAt", "lastActiveAt", "lastLogin"):
                val = dates.get(key)
                if isinstance(val, str) and val:
                    return val
        # Fall back to root-level keys
        for key in ("lastLoginAt", "lastSeenAt", "lastOnlineAt", "lastActiveAt", "lastLogin"):
            val = obj.get(key)
            if isinstance(val, str) and val:
                return val
        return None
