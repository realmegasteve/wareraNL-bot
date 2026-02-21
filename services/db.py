import aiosqlite
import logging
from typing import Optional

logger = logging.getLogger("services.db")


class Database:
    """Simple async wrapper around a SQLite file for storing poll state and job metadata.

    This is intentionally small: add migrations/ORM or switch to Postgres/asyncpg later
    if you need more scale.
    """

    def __init__(self, path: str = "database/external.db") -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def setup(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT,
                progress INTEGER,
                result_path TEXT
            )
            """
        )
        # store latest snapshot per country
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS country_snapshots (
                country_id TEXT PRIMARY KEY,
                code TEXT,
                name TEXT,
                specialized_item TEXT,
                production_bonus REAL,
                raw_json TEXT,
                updated_at TEXT
            )
            """
        )

        # store current top per specialization item (permanent bonus, no deposit)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS specialization_top (
                item TEXT PRIMARY KEY,
                country_id TEXT,
                country_name TEXT,
                production_bonus REAL,
                strategic_bonus REAL,
                ethic_bonus REAL,
                ethic_deposit_bonus REAL,
                updated_at TEXT
            )
            """
        )
        # migrations: add breakdown columns if missing
        for col in ("strategic_bonus REAL", "ethic_bonus REAL", "ethic_deposit_bonus REAL"):
            try:
                await self._conn.execute(f"ALTER TABLE specialization_top ADD COLUMN {col}")
                await self._conn.commit()
            except Exception:
                pass  # column already exists

        # store current deposit top per specialization item
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deposit_top (
                item TEXT PRIMARY KEY,
                region_id TEXT,
                region_name TEXT,
                country_id TEXT,
                country_name TEXT,
                bonus INTEGER,
                deposit_bonus REAL,
                ethic_deposit_bonus REAL,
                permanent_bonus REAL,
                deposit_end_at TEXT,
                updated_at TEXT
            )
            """
        )
        # migrations: add breakdown columns if missing
        for col in ("region_name TEXT", "deposit_bonus REAL", "ethic_deposit_bonus REAL"):
            try:
                await self._conn.execute(f"ALTER TABLE deposit_top ADD COLUMN {col}")
                await self._conn.commit()
            except Exception:
                pass  # column already exists        # citizen level cache (populated by the daily background task)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS citizen_levels (
                user_id TEXT PRIMARY KEY,
                country_id TEXT NOT NULL,
                level INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_citizen_levels_country ON citizen_levels(country_id)"
        )
        await self._conn.commit()
        logger.info("Database initialized at %s", self.path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def get_poll_state(self, key: str) -> Optional[str]:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        async with self._conn.execute("SELECT value FROM poll_state WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_poll_state(self, key: str, value: str) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute(
            "INSERT INTO poll_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self._conn.commit()

    async def create_job(self, job_id: str) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute("INSERT OR REPLACE INTO jobs(id, status, progress) VALUES(?, ?, ?)", (job_id, "pending", 0))
        await self._conn.commit()

    async def update_job_progress(self, job_id: str, progress: int, status: Optional[str] = None) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        if status:
            await self._conn.execute("UPDATE jobs SET progress = ?, status = ? WHERE id = ?", (progress, status, job_id))
        else:
            await self._conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (progress, job_id))
        await self._conn.commit()

    async def save_country_snapshot(self, country_id: str, code: str | None, name: str | None, specialized_item: str | None, production_bonus: float | None, raw_json: str, updated_at: str) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute(
            "INSERT OR REPLACE INTO country_snapshots(country_id, code, name, specialized_item, production_bonus, raw_json, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (country_id, code, name, specialized_item, production_bonus, raw_json, updated_at),
        )
        await self._conn.commit()

    async def get_top_specialization(self, item: str) -> Optional[dict]:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        async with self._conn.execute("SELECT country_id, country_name, production_bonus, strategic_bonus, ethic_bonus, ethic_deposit_bonus, updated_at FROM specialization_top WHERE item = ?", (item,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {"country_id": row[0], "country_name": row[1], "production_bonus": row[2], "strategic_bonus": row[3], "ethic_bonus": row[4], "ethic_deposit_bonus": row[5], "updated_at": row[6]}

    async def get_all_tops(self) -> list:
        """Return all specialization tops as a list of dicts."""
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        rows = []
        async with self._conn.execute("SELECT item, country_id, country_name, production_bonus, strategic_bonus, ethic_bonus, ethic_deposit_bonus, updated_at FROM specialization_top") as cur:
            async for row in cur:
                rows.append({"item": row[0], "country_id": row[1], "country_name": row[2], "production_bonus": row[3], "strategic_bonus": row[4], "ethic_bonus": row[5], "ethic_deposit_bonus": row[6], "updated_at": row[7]})
        return rows

    async def delete_top_specialization(self, item: str) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute("DELETE FROM specialization_top WHERE item = ?", (item,))
        await self._conn.commit()

    async def set_top_specialization(self, item: str, country_id: str, country_name: str, production_bonus: float, updated_at: str, strategic_bonus: float | None = None, ethic_bonus: float | None = None, ethic_deposit_bonus: float | None = None) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute(
            "INSERT OR REPLACE INTO specialization_top(item, country_id, country_name, production_bonus, strategic_bonus, ethic_bonus, ethic_deposit_bonus, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (item, country_id, country_name, production_bonus, strategic_bonus, ethic_bonus, ethic_deposit_bonus, updated_at),
        )
        await self._conn.commit()

    async def get_deposit_top(self, item: str) -> dict | None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        async with self._conn.execute(
            "SELECT region_id, region_name, country_id, country_name, bonus, deposit_bonus, ethic_deposit_bonus, permanent_bonus, deposit_end_at, updated_at FROM deposit_top WHERE item = ?",
            (item,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "region_id": row[0], "region_name": row[1], "country_id": row[2],
                "country_name": row[3], "bonus": row[4], "deposit_bonus": row[5],
                "ethic_deposit_bonus": row[6], "permanent_bonus": row[7],
                "deposit_end_at": row[8], "updated_at": row[9],
            }

    async def get_all_deposit_tops(self) -> list[dict]:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        rows = []
        async with self._conn.execute(
            "SELECT item, region_id, region_name, country_id, country_name, bonus, deposit_bonus, ethic_deposit_bonus, permanent_bonus, deposit_end_at, updated_at FROM deposit_top"
        ) as cur:
            async for row in cur:
                rows.append({
                    "item": row[0], "region_id": row[1], "region_name": row[2],
                    "country_id": row[3], "country_name": row[4], "bonus": row[5],
                    "deposit_bonus": row[6], "ethic_deposit_bonus": row[7],
                    "permanent_bonus": row[8], "deposit_end_at": row[9], "updated_at": row[10],
                })
        return rows

    async def set_deposit_top(
        self, item: str, region_id: str, region_name: str, country_id: str, country_name: str,
        bonus: int, deposit_bonus: float, ethic_deposit_bonus: float,
        permanent_bonus: float, deposit_end_at: str, updated_at: str,
    ) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute(
            "INSERT OR REPLACE INTO deposit_top(item, region_id, region_name, country_id, country_name, bonus, deposit_bonus, ethic_deposit_bonus, permanent_bonus, deposit_end_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item, region_id, region_name, country_id, country_name, bonus, deposit_bonus, ethic_deposit_bonus, permanent_bonus, deposit_end_at, updated_at),
        )
        await self._conn.commit()

    async def upsert_citizen_level(self, user_id: str, country_id: str, level: int, updated_at: str) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute(
            "INSERT OR REPLACE INTO citizen_levels(user_id, country_id, level, updated_at) VALUES(?, ?, ?, ?)",
            (user_id, country_id, level, updated_at),
        )

    async def flush_citizen_levels(self) -> None:
        """Commit any pending citizen level upserts."""
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.commit()

    async def delete_citizens_for_country(self, country_id: str) -> None:
        """Remove stale citizen rows for a country before a fresh refresh."""
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute("DELETE FROM citizen_levels WHERE country_id = ?", (country_id,))
        await self._conn.commit()

    async def get_level_distribution(self, country_id: str) -> tuple[dict[int, int], str | None]:
        """Return (level_counts, last_updated_at) for a country from cache."""
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        counts: dict[int, int] = {}
        last_updated: str | None = None
        async with self._conn.execute(
            "SELECT level, updated_at FROM citizen_levels WHERE country_id = ?", (country_id,)
        ) as cur:
            async for row in cur:
                lvl, updated_at = row
                if lvl is not None:
                    counts[int(lvl)] = counts.get(int(lvl), 0) + 1
                if last_updated is None or updated_at > last_updated:
                    last_updated = updated_at
        return counts, last_updated


__all__ = ["Database"]
