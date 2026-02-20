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

        # store current top per specialization item
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS specialization_top (
                item TEXT PRIMARY KEY,
                country_id TEXT,
                country_name TEXT,
                production_bonus REAL,
                updated_at TEXT
            )
            """
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
        async with self._conn.execute("SELECT country_id, country_name, production_bonus, updated_at FROM specialization_top WHERE item = ?", (item,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return {"country_id": row[0], "country_name": row[1], "production_bonus": row[2], "updated_at": row[3]}

    async def get_all_tops(self) -> list:
        """Return all specialization tops as a list of dicts.

        Each item is: {item, country_id, country_name, production_bonus, updated_at}
        """
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        rows = []
        async with self._conn.execute("SELECT item, country_id, country_name, production_bonus, updated_at FROM specialization_top") as cur:
            async for row in cur:
                rows.append({"item": row[0], "country_id": row[1], "country_name": row[2], "production_bonus": row[3], "updated_at": row[4]})
        return rows

    async def set_top_specialization(self, item: str, country_id: str, country_name: str, production_bonus: float, updated_at: str) -> None:
        if not self._conn:
            raise RuntimeError("Database not initialized; call setup() first")
        await self._conn.execute("INSERT OR REPLACE INTO specialization_top(item, country_id, country_name, production_bonus, updated_at) VALUES(?, ?, ?, ?, ?)", (item, country_id, country_name, production_bonus, updated_at))
        await self._conn.commit()


__all__ = ["Database"]
