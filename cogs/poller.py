"""Production & citizen tracking cog for the WarEra Discord bot."""

import json
import logging
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ext.commands import Context
import asyncio

from services.api_client import APIClient
from services.db import Database
from services.citizen_cache import CitizenCache
from services.country_utils import extract_country_list, find_country, country_id as cid_of

logger = logging.getLogger("discord_bot")

# Role IDs allowed to run privileged commands (in addition to bot owner)
_PRIVILEGED_ROLE_IDS: set[int] = {
    1451180288515506258,  # minister_foreign_affairs / ambassadeur
    1401530996725383178,  # president
    1401531414553428139,  # vice_president
    1458527742646816892,  # government
    1458427087189835776,  # commandant
}


def _has_privileged_role() -> bool:
    """app_commands check: owner OR one of the privileged roles (bypassed in test mode)."""
    async def predicate(interaction: discord.Interaction) -> bool:
        bot = interaction.client
        # In test mode everyone is allowed
        if getattr(bot, "testing", False):
            return True
        # Bot owner is always allowed
        app_info = await bot.application_info()
        if interaction.user.id == app_info.owner.id:
            return True
        if interaction.guild and isinstance(interaction.user, discord.Member):
            user_role_ids = {r.id for r in interaction.user.roles}
            if user_role_ids & _PRIVILEGED_ROLE_IDS:
                return True
        raise app_commands.MissingPermissions(["privileged_role"])
    return app_commands.check(predicate)


class ProductionChecker(commands.Cog, name="production_checker"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.config = getattr(self.bot, "config", {}) or {}
        self._client: APIClient | None = None
        self._db: Database | None = None
        self._citizen_cache: CitizenCache | None = None
        self._poll_lock: asyncio.Lock = asyncio.Lock()

    def cog_load(self) -> None:
        asyncio.create_task(self._ensure_services_and_start())

    def cog_unload(self) -> None:
        self.hourly_production_check.cancel()
        self.daily_citizen_refresh.cancel()
        if self._client:
            asyncio.create_task(self._client.close())
        if self._db:
            asyncio.create_task(self._db.close())

    async def _ensure_services_and_start(self) -> None:
        base_url = self.config.get("api_base_url", "https://api.example.local")
        db_path = self.config.get("external_db_path", "database/external.db")
        api_keys = None
        try:
            with open("_api_keys.json", "r") as kf:
                api_keys = json.load(kf).get("keys", [])
        except Exception:
            self.bot.logger.debug("No _api_keys.json found or failed to parse")

        self._client = APIClient(base_url=base_url, api_keys=api_keys)
        await self._client.start()
        self._db = Database(db_path)
        await self._db.setup()
        self._citizen_cache = CitizenCache(self._client, self._db)

        self.hourly_production_check.start()

    # ------------------------------------------------------------------ #
    # Hourly production poll                                               #
    # ------------------------------------------------------------------ #

    @tasks.loop(minutes=15)
    async def hourly_production_check(self):
        """Scheduled wrapper that ensures only one poll runs at a time."""
        import time
        self.bot.logger.info("[production poll] starting")
        t0 = time.monotonic()
        async with self._poll_lock:
            changes = await self._run_poll_once()
        elapsed = time.monotonic() - t0
        if changes:
            self.bot.logger.info(
                "[production poll] done in %.1fs — %d change(s): %s",
                elapsed,
                len(changes),
                ", ".join(f"{item}: {old} → {new}" for item, old, new in changes),
            )
        else:
            self.bot.logger.info("[production poll] done in %.1fs — no changes", elapsed)

    @hourly_production_check.before_loop
    async def before_hourly_production_check(self):
        await self.bot.wait_until_ready()
        # Skip the immediate first fire — wait one full interval before polling
        interval = (
            self.hourly_production_check.hours * 3600
            + self.hourly_production_check.minutes * 60
            + self.hourly_production_check.seconds
        )
        await asyncio.sleep(interval)

    async def _run_poll_once(self) -> list[tuple[str, str, str]]:
        """Perform a single production poll using getRecommendedRegionIdsByItemCode.

        Tracks two tops per item:
          - Permanent leader: highest (strategicBonus + ethicSpecializationBonus)
          - Deposit top: highest total bonus where a deposit is active

        Returns a list of change tuples: (label, old_desc, new_desc).
        """
        self.bot.logger.info("Starting production poll...")
        try:
            market_channel_id = self.config.get("channels", {}).get("production")
            if not market_channel_id:
                self.bot.logger.warning("Market channel ID not configured")
                return []
            if not self._client or not self.config.get("api_base_url"):
                self.bot.logger.warning("API client or api_base_url not configured")
                return []

            try:
                all_countries = await self._client.get("/country.getAllCountries")
            except Exception:
                self.bot.logger.exception("Failed to fetch country list")
                return []

            country_list = extract_country_list(all_countries)
            if not country_list:
                return []

            now = datetime.utcnow().isoformat() + "Z"

            # cid → country object — used to look up name from a country ID
            cid_to_country: dict[str, dict] = {cid_of(c): c for c in country_list}

            # items_to_poll: set of item codes that have at least one specialized country
            items_to_poll: set[str] = set()
            for country in country_list:
                item = (
                    country.get("specializedItem")
                    or country.get("specialized_item")
                    or country.get("specialization")
                )
                if not item:
                    continue
                items_to_poll.add(item)
                if self._db:
                    pb = self._get_permanent_bonus(country)
                    try:
                        await self._db.save_country_snapshot(
                            cid_of(country), country.get("code"), country.get("name"),
                            item, pb, json.dumps(country, default=str), now,
                        )
                    except Exception:
                        self.bot.logger.exception("Failed to save snapshot for country %s", cid_of(country))

            # Build regionId → countryId map from region.getRegionsObject.
            # Each region object contains a "country" field = current owner's countryId.
            region_to_cid: dict[str, str] = {}
            try:
                regions_resp = await self._client.get("/region.getRegionsObject")
                regions_data = (
                    regions_resp.get("result", {}).get("data", {})
                    if isinstance(regions_resp, dict) else {}
                )
                if isinstance(regions_data, dict):
                    for rid, robj in regions_data.items():
                        cid = robj.get("country") if isinstance(robj, dict) else None
                        if cid:
                            region_to_cid[rid] = cid
                region_to_name: dict[str, str] = {
                    rid: robj.get("name", rid)
                    for rid, robj in (regions_data.items() if isinstance(regions_data, dict) else [])
                    if isinstance(robj, dict)
                }
            except Exception:
                self.bot.logger.exception("Failed to fetch region map; deposit names will be unavailable")
                region_to_name = {}

            changes: list[tuple[str, str, str]] = []
            for item in items_to_poll:
                try:
                    resp = await self._client.get(
                        "/company.getRecommendedRegionIdsByItemCode",
                        params={"input": json.dumps({"itemCode": item})},
                    )
                except Exception:
                    self.bot.logger.exception("Failed to fetch recommended regions for %s", item)
                    continue

                region_list = self._unwrap_region_list(resp)
                if not region_list:
                    continue

                # ---- Long-term leader: max(strategic + ethicSpec + ethicDeposit) ----
                # ethicDepositBonus is semi-permanent (party ethics), only raw depositBonus is temporary
                top_perm = max(
                    region_list,
                    key=lambda r: (r.get("strategicBonus") or 0) + (r.get("ethicSpecializationBonus") or 0) + (r.get("ethicDepositBonus") or 0),
                )
                perm_strategic = top_perm.get("strategicBonus") or 0
                perm_ethic = top_perm.get("ethicSpecializationBonus") or 0
                perm_ethic_dep = top_perm.get("ethicDepositBonus") or 0
                perm_bonus = perm_strategic + perm_ethic + perm_ethic_dep
                perm_rid = top_perm.get("regionId") or top_perm.get("region_id") or ""
                perm_cid = region_to_cid.get(perm_rid)
                perm_name = cid_to_country[perm_cid]["name"] if perm_cid in cid_to_country else "Unknown"

                if perm_bonus > 0:
                    change = await self._handle_permanent_leader(
                        item, perm_cid or "unknown", perm_name, perm_bonus,
                        perm_strategic, perm_ethic, perm_ethic_dep, now, market_channel_id
                    )
                    if change:
                        changes.append(change)
                else:
                    # Strategic bonus has dropped to 0 for all regions — clear any stale DB entry
                    if self._db:
                        try:
                            await self._db.delete_top_specialization(item)
                        except Exception:
                            self.bot.logger.exception("Failed to clear stale permanent leader for %s", item)

                # ---- Short-term top (longest remaining deposit duration) ----
                deposit_regions = [r for r in region_list if (r.get("depositBonus") or 0) > 0]
                if deposit_regions:
                    def _end_ts(r: dict) -> float:
                        raw = r.get("depositEndAt") or r.get("deposit_end_at") or ""
                        try:
                            from datetime import timezone
                            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                        except Exception:
                            return 0.0

                    top_dep = max(deposit_regions, key=_end_ts)
                    dep_total = top_dep.get("bonus") or 0
                    dep_deposit_raw = top_dep.get("depositBonus") or 0
                    dep_ethic_dep_raw = top_dep.get("ethicDepositBonus") or 0
                    dep_perm = (top_dep.get("strategicBonus") or 0) + (top_dep.get("ethicSpecializationBonus") or 0)
                    dep_rid = top_dep.get("regionId") or top_dep.get("region_id") or ""
                    dep_region_name = region_to_name.get(dep_rid, dep_rid)
                    dep_cid = region_to_cid.get(dep_rid)
                    dep_name = cid_to_country[dep_cid]["name"] if dep_cid in cid_to_country else "Unknown"
                    dep_end_at = top_dep.get("depositEndAt") or top_dep.get("deposit_end_at") or ""

                    change = await self._handle_deposit_top(
                        item, dep_rid, dep_region_name, dep_cid or "unknown", dep_name,
                        dep_total, dep_deposit_raw, dep_ethic_dep_raw, dep_perm, dep_end_at, now, market_channel_id,
                    )
                    if change:
                        changes.append(change)

        except Exception as e:
            self.bot.logger.error("Error in production poll: %s", e)
            return []

        return changes

    @staticmethod
    def _get_permanent_bonus(country: dict) -> float | None:
        """Country's permanent production bonus (strategic + party ethics, no deposit)."""
        try:
            rb = country.get("rankings", {}).get("countryProductionBonus")
            if isinstance(rb, dict) and "value" in rb:
                return float(rb["value"])
        except Exception:
            pass
        return None

    @staticmethod
    def _unwrap_region_list(api_response) -> list[dict]:
        if isinstance(api_response, list):
            return [r for r in api_response if isinstance(r, dict)]
        if isinstance(api_response, dict):
            result = api_response.get("result")
            if isinstance(result, dict):
                data = result.get("data")
                if isinstance(data, list):
                    return [r for r in data if isinstance(r, dict)]
            for key in ("data", "items", "regions"):
                v = api_response.get(key)
                if isinstance(v, list):
                    return [r for r in v if isinstance(r, dict)]
        return []

    async def _handle_permanent_leader(
        self, item: str, country_id: str, country_name: str,
        bonus: float, strategic_bonus: float, ethic_bonus: float, ethic_deposit_bonus: float,
        now: str, channel_id: int,
    ) -> tuple | None:
        try:
            prev = await self._db.get_top_specialization(item) if self._db else None
        except Exception:
            prev = None

        changed = (
            prev is None
            or abs(float(prev.get("production_bonus") or 0) - float(bonus)) > 0.01
        )

        if changed and prev is not None:
            old_desc = f"{prev.get('country_name')} ({prev.get('production_bonus')}%)"
            for guild in self.bot.guilds:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(
                            f"🏭 **{item}** permanent leader: **{country_name}** ({bonus}%) — was {old_desc}"
                        )
                    except Exception:
                        self.bot.logger.exception("Failed sending permanent leader update for %s", item)

        if self._db:
            try:
                await self._db.set_top_specialization(
                    item, country_id, country_name, float(bonus), now,
                    strategic_bonus=strategic_bonus, ethic_bonus=ethic_bonus,
                    ethic_deposit_bonus=ethic_deposit_bonus,
                )
            except Exception:
                self.bot.logger.exception("Failed to persist permanent leader for %s", item)

        if changed and prev is not None:
            old_desc = f"{prev.get('country_name')} ({prev.get('production_bonus')}%)"
            return (item, old_desc, f"{country_name} ({bonus}%)")
        return None

    async def _handle_deposit_top(
        self, item: str, region_id: str, region_name: str, country_id: str, country_name: str,
        bonus: int, deposit_bonus: float, ethic_deposit_bonus: float,
        permanent_bonus: float, deposit_end_at: str, now: str, channel_id: int,
    ) -> tuple | None:
        try:
            prev = await self._db.get_deposit_top(item) if self._db else None
        except Exception:
            prev = None

        def _ts(iso: str) -> float:
            try:
                return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0

        prev_end_ts = _ts(prev.get("deposit_end_at") or "") if prev else 0.0
        new_end_ts = _ts(deposit_end_at)
        is_new = prev is None
        # Report a change when the region changed OR the deposit lasts longer than what we stored
        changed = is_new or (prev.get("region_id") != region_id) or (new_end_ts > prev_end_ts + 60)

        if changed and not is_new:
            duration = self._format_duration(deposit_end_at)
            for guild in self.bot.guilds:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(
                            f"⚡ **{item}** new short-term leader: **{region_name}** — "
                            f"**{bonus}%** total"
                            + (f" ⏳ {duration}" if duration else "")
                        )
                    except Exception:
                        self.bot.logger.exception("Failed sending deposit update for %s", item)

        if self._db:
            try:
                await self._db.set_deposit_top(
                    item, region_id, region_name, country_id, country_name,
                    bonus, deposit_bonus, ethic_deposit_bonus, permanent_bonus, deposit_end_at, now,
                )
            except Exception:
                self.bot.logger.exception("Failed to persist deposit top for %s", item)

        # Only emit a change tuple when the leader actually changed
        if changed and not is_new:
            old_region = prev.get("region_name") or prev.get("region_id") or "?"
            old_bonus = prev.get("bonus") or 0
            return (f"{item} [deposit]", f"{old_region} ({old_bonus}%)", f"{region_name} ({bonus}%)")
        return None

    @staticmethod
    def _format_duration(iso_str: str) -> str | None:
        from datetime import timezone
        try:
            end = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            delta = end - datetime.now(timezone.utc)
            if delta.total_seconds() <= 0:
                return "expired"
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            if hours >= 24:
                days, hrs = divmod(hours, 24)
                return f"{days}d {hrs}h" if hrs else f"{days}d"
            return f"{hours}h {minutes}m" if minutes else f"{hours}h"
        except Exception:
            return None

    @staticmethod
    def _pct(v) -> str:
        try:
            return f"{float(v):.2f}%"
        except (TypeError, ValueError):
            return "0%"

    @staticmethod
    def _long_bd(t: dict) -> str:
        parts: list[str] = []
        if t.get("strategic_bonus"): parts.append(f"{t['strategic_bonus']}% strat")
        if t.get("ethic_bonus"): parts.append(f"{t['ethic_bonus']}% eth")
        if t.get("ethic_deposit_bonus"): parts.append(f"{t['ethic_deposit_bonus']}% eth.dep")
        return " + ".join(parts)

    @staticmethod
    def _short_bd(d: dict) -> str:
        parts: list[str] = []
        if d.get("permanent_bonus"): parts.append(f"{d['permanent_bonus']}% perm")
        if d.get("deposit_bonus"): parts.append(f"{d['deposit_bonus']}% dep")
        if d.get("ethic_deposit_bonus"): parts.append(f"{d['ethic_deposit_bonus']}% eth.dep")
        return " + ".join(parts)

    # ------------------------------------------------------------------ #
    # Daily citizen level cache                                            #
    # ------------------------------------------------------------------ #

    @tasks.loop(hours=24)
    async def daily_citizen_refresh(self):
        """Refresh citizen level cache for every country once per day.

        Uses poll_state to persist the last-run timestamp so restarts don't
        trigger a duplicate refresh within the same 24-hour window.
        """
        if not self._client or not self._db or not self._citizen_cache:
            return

        from datetime import timezone
        now_utc = datetime.now(timezone.utc)

        # Check if a refresh already happened in the last 24 hours
        try:
            last_run_str = await self._db.get_poll_state("citizen_refresh_last_run")
            if last_run_str:
                last_run = datetime.fromisoformat(last_run_str)
                elapsed_h = (now_utc - last_run).total_seconds() / 3600
                if elapsed_h < 24:
                    self.bot.logger.info(
                        "daily_citizen_refresh: skipping — last run %.1fh ago (< 24h)", elapsed_h
                    )
                    return
        except Exception:
            self.bot.logger.exception("daily_citizen_refresh: failed to read last-run state")

        self.bot.logger.info("daily_citizen_refresh: starting full country sweep")
        try:
            all_countries = await self._client.get("/country.getAllCountries")
        except Exception:
            self.bot.logger.exception("daily_citizen_refresh: failed to fetch countries")
            return

        # Persist the start time before the sweep so a crash mid-run doesn't
        # cause an immediate retry on next restart.
        try:
            await self._db.set_poll_state("citizen_refresh_last_run", now_utc.isoformat())
        except Exception:
            self.bot.logger.exception("daily_citizen_refresh: failed to save last-run state")

        country_list = extract_country_list(all_countries)
        total = len(country_list)
        for i, country in enumerate(country_list, 1):
            cid = cid_of(country)
            name = country.get("name", cid)
            self.bot.logger.info("daily_citizen_refresh: (%d/%d) %s", i, total, name)
            try:
                await self._citizen_cache.refresh_country(cid, name)
            except Exception:
                self.bot.logger.exception("daily_citizen_refresh: error refreshing %s", name)
            await asyncio.sleep(2)
        self.bot.logger.info("daily_citizen_refresh: complete (%d countries)", total)

    @daily_citizen_refresh.before_loop
    async def before_daily_citizen_refresh(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ #
    # Commands — production                                                #
    # ------------------------------------------------------------------ #

    @commands.command(name="poll_now")
    @commands.is_owner()
    async def poll_now(self, ctx: Context):
        """Trigger a single production poll immediately."""
        if not self._client:
            await ctx.send("API client not initialized.")
            return
        if self._poll_lock.locked():
            await ctx.send("A production poll is already running.")
            return
        channel = ctx.channel
        await channel.send("Starting production poll...")

        async def _run_and_report():
            async with self._poll_lock:
                changes = await self._run_poll_once()
            if not changes:
                try:
                    await channel.send("Production poll completed: no leadership changes detected.")
                except Exception:
                    self.bot.logger.exception("Failed to send poll completion message")
                return
            try:
                lines = []
                for item, prev, new in changes:
                    is_deposit = item.endswith(" [deposit]")
                    base = item[:-10] if is_deposit else item
                    # new/prev are like "Turkey (62.75%)" or "Bahamas (73%)"
                    if is_deposit:
                        lines.append(
                            f"⚡ Deposit **{base}** new leader: **{new}** ← was {prev}"
                        )
                    else:
                        lines.append(
                            f"🏭 Specialization **{base}** new leader: **{new}** ← was {prev}"
                        )
                await channel.send(f"Production poll — {len(changes)} change(s):\n" + "\n".join(lines))
            except Exception:
                self.bot.logger.exception("Failed to send poll report")

        asyncio.create_task(_run_and_report())

    @commands.command(name="fake_leader")
    @commands.is_owner()
    async def fake_leader(self, ctx: Context):
        """Set all stored production bonuses to 0 so the next !poll_now reports changes.

        Useful for testing: run !fake_leader, then !poll_now.  Every item whose
        actual bonus is > 0 will appear as a leadership change.
        """
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        try:
            await self._db._conn.execute("UPDATE specialization_top SET production_bonus = 0")
            await self._db._conn.execute("UPDATE deposit_top SET bonus = 0")
            await self._db._conn.commit()
            rows = await self._db._conn.execute("SELECT COUNT(*) FROM specialization_top")
            count = (await rows.fetchone())[0]
            if count == 0:
                await ctx.send(
                    "Tables are empty — run `!poll_now` first to populate them, then `!fake_leader`, then `!poll_now` again."
                )
            else:
                await ctx.send(
                    f"All stored bonuses zeroed ({count} items). Run `!poll_now` — every item with a real bonus will show as a new leader."
                )
        except Exception:
            self.bot.logger.exception("fake_leader: failed to update DB")
            await ctx.send("DB update failed; see logs.")

    # ------------------------------------------------------------------ #
    # Helper — primary colour for embeds                                   #
    # ------------------------------------------------------------------ #

    def _embed_colour(self) -> discord.Colour:
        raw = (self.config.get("colors") or {}).get("primary", "0xffb612")
        try:
            return discord.Colour(int(str(raw), 16))
        except Exception:
            return discord.Colour.gold()

    # ------------------------------------------------------------------ #
    # /bonus                                                               #
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="bonus", description="Show production leaders for every item.")
    async def bonus(self, ctx: Context):
        """Display the current production leaders for each specialization."""
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        if hasattr(ctx, 'defer'):
            await ctx.defer()
        try:
            tops = await self._db.get_all_tops()
        except Exception:
            self.bot.logger.exception("Failed to fetch production leaders")
            await ctx.send("Failed to fetch production leaders; see logs.")
            return

        deposit_tops: list[dict] = []
        try:
            deposit_tops = await self._db.get_all_deposit_tops()
        except Exception:
            pass

        if not tops and not deposit_tops:
            await ctx.send("No production leaders recorded.")
            return

        dep_by_item = {d.get("item"): d for d in deposit_tops}
        top_by_item = {t.get("item"): t for t in tops}
        all_items = sorted(set(top_by_item) | set(dep_by_item))

        long_rows = [(item, top_by_item[item]) for item in all_items if item in top_by_item]
        short_rows = [(item, dep_by_item[item]) for item in all_items if item in dep_by_item]

        best_l_idx = (
            max(range(len(long_rows)), key=lambda i: float(long_rows[i][1].get("production_bonus") or 0))
            if long_rows else None
        )
        best_s_idx = (
            max(range(len(short_rows)), key=lambda i: float(short_rows[i][1].get("bonus") or 0))
            if short_rows else None
        )

        colour = self._embed_colour()

        # ── Long-term embed ──────────────────────────────────────────────
        if long_rows:
            wi = max(max(len(item) for item, _ in long_rows), 4)
            wc = max(max(len(t.get("country_name") or "") for _, t in long_rows), 7)
            wb = max(max(len(self._pct(t.get("production_bonus"))) for _, t in long_rows), 5)
            bds_l = [self._long_bd(t) for _, t in long_rows]
            wbd = max(max(len(bd) for bd in bds_l), 9)
            hdr_l = f"  {'Item':<{wi}}  {'Country':<{wc}}  {'Bonus':>{wb}}  {'Breakdown':<{wbd}}"
            sep_l = "  " + "-" * (len(hdr_l) - 2)
            rows_l = [
                f"{'>' if i == best_l_idx else ' '} {item:<{wi}}  {(t.get('country_name') or 'Unknown'):<{wc}}  {self._pct(t.get('production_bonus')):>{wb}}  {bd:<{wbd}}"
                for i, ((item, t), bd) in enumerate(zip(long_rows, bds_l))
            ]
            table_l = "\n".join([hdr_l, sep_l] + rows_l)
        else:
            table_l = "(none)"

        if short_rows:
            wi2 = max(max(len(item) for item, _ in short_rows), 4)
            wr = max(max(len(d.get("region_name") or d.get("region_id") or "") for _, d in short_rows), 6)
            wb2 = max(max(len(self._pct(d.get("bonus"))) for _, d in short_rows), 5)
            bds_s = [self._short_bd(d) for _, d in short_rows]
            durs = [self._format_duration(d.get("deposit_end_at") or "") or "" for _, d in short_rows]
            wbd2 = max(max(len(bd) for bd in bds_s), 9)
            wdur = max(max(len(dur) for dur in durs), 7)
            hdr_s = f"  {'Item':<{wi2}}  {'Region':<{wr}}  {'Bonus':>{wb2}}  {'Breakdown':<{wbd2}}  {'Expires':<{wdur}}"
            sep_s = "  " + "-" * (len(hdr_s) - 2)
            rows_s = [
                f"{'>' if i == best_s_idx else ' '} {item:<{wi2}}  {(d.get('region_name') or d.get('region_id') or '?'):<{wr}}  {self._pct(d.get('bonus')):>{wb2}}  {bd:<{wbd2}}  {dur:<{wdur}}"
                for i, ((item, d), bd, dur) in enumerate(zip(short_rows, bds_s, durs))
            ]
            table_s = "\n".join([hdr_s, sep_s] + rows_s)
        else:
            table_s = "(none)"

        MSG_LIMIT = 1900  # plain message limit with safe margin

        async def _send_table(title: str, table_text: str) -> None:
            """Send table as plain code-block message(s) — full channel width."""
            lines = table_text.splitlines()
            header_lines = lines[:2]
            data_lines = lines[2:]
            chunks: list[list[str]] = []
            chunk: list[str] = []
            for line in data_lines:
                body = "\n".join(header_lines + chunk + [line])
                if len(f"**{title}**\n```\n{body}\n```") > MSG_LIMIT and chunk:
                    chunks.append(chunk)
                    chunk = [line]
                else:
                    chunk.append(line)
            if chunk:
                chunks.append(chunk)
            for idx, ch in enumerate(chunks):
                chunk_title = title if idx == 0 else f"{title} (cont.)"
                block = f"**{chunk_title}**\n```\n" + "\n".join(header_lines + ch) + "\n```"
                await ctx.send(block)

        await _send_table("📈 Long-term leaders", table_l)
        await _send_table("⚡ Short-term leaders", table_s)

        # Best-of summary as a compact embed
        best_embed = discord.Embed(colour=colour)
        if best_l_idx is not None:
            bl_item, bl = long_rows[best_l_idx]
            best_embed.add_field(
                name="🏆 Best long-term",
                value=f"**{bl_item}** — {bl.get('country_name')} **{bl.get('production_bonus')}%**",
                inline=False,
            )
        if best_s_idx is not None:
            bs_item, bs = short_rows[best_s_idx]
            rl = bs.get("region_name") or bs.get("region_id") or "?"
            dur = self._format_duration(bs.get("deposit_end_at") or "")
            best_embed.add_field(
                name="⚡ Best short-term",
                value=(
                    f"**{bs_item}** — {rl} **{bs.get('bonus')}%**"
                    + (f"  ⏳ {dur}" if dur else "")
                ),
                inline=False,
            )
        if best_embed.fields:
            await ctx.send(embed=best_embed)

    # ------------------------------------------------------------------ #
    # /topbonus                                                            #
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="topbonus", description="Show the single best long-term and short-term bonus.")
    async def topbonus(self, ctx: Context):
        """Show the single best long-term and best short-term production bonus."""
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        if hasattr(ctx, 'defer'):
            await ctx.defer()
        tops: list[dict] = []
        deposit_tops: list[dict] = []
        try:
            tops = await self._db.get_all_tops()
            deposit_tops = await self._db.get_all_deposit_tops()
        except Exception:
            self.bot.logger.exception("Failed to fetch production data")
            await ctx.send("Failed to fetch production data; see logs.")
            return

        if not tops and not deposit_tops:
            await ctx.send("No production data recorded yet.")
            return

        colour = self._embed_colour()
        embed = discord.Embed(title="Top Production Bonuses", colour=colour)

        if tops:
            bl = max(tops, key=lambda t: float(t.get("production_bonus") or 0))
            bd = self._long_bd(bl)
            embed.add_field(
                name="🏆 Best long-term",
                value=(
                    f"**{bl.get('item')}** — {bl.get('country_name')} **{bl.get('production_bonus')}%**"
                    + (f"\n*{bd}*" if bd else "")
                ),
                inline=False,
            )
        if deposit_tops:
            bs = max(deposit_tops, key=lambda d: float(d.get("bonus") or 0))
            rl = bs.get("region_name") or bs.get("region_id") or "?"
            dur = self._format_duration(bs.get("deposit_end_at") or "")
            bd = self._short_bd(bs)
            embed.add_field(
                name="⚡ Best short-term",
                value=(
                    f"**{bs.get('item')}** — {rl} **{bs.get('bonus')}%**"
                    + (f"  ⏳ {dur}" if dur else "")
                    + (f"\n*{bd}*" if bd else "")
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="clear_production")
    @commands.is_owner()
    async def clear_production(self, ctx: Context):
        """Wipe all rows from specialization_top and deposit_top.

        The next poll will repopulate them from scratch.
        """
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        try:
            await self._db._conn.execute("DELETE FROM specialization_top")
            await self._db._conn.execute("DELETE FROM deposit_top")
            await self._db._conn.commit()
            await ctx.send("✅ Cleared `specialization_top` and `deposit_top`. Run `!poll_now` to repopulate.")
        except Exception:
            self.bot.logger.exception("Failed to clear production tables")
            await ctx.send("Failed to clear tables; see logs.")

    @commands.hybrid_command(name="movecost", description="Show break-even hours to recover company relocation cost.")
    @app_commands.describe(
        bonuses='Optional: current bonus, or "current new" (e.g. "30" or "30 55"). Leave blank for full table.',
    )
    async def movecost(self, ctx: Context, bonuses: str = ""):
        """Break-even table: hours of Automated Engine production to recover the 5-concrete move cost.

        Only the bonus *gain* counts — your engine's base output runs regardless of location.
        Rows = new production bonus (5 %–80 %), columns = automated engine level (1–7).
        Rows at or below your current bonus are shown as ∞ (moving gives no gain there).
        Colour: green ≤ 72 h, yellow 73–120 h, red > 120 h / ∞.
        Usage: ``/movecost``  ``/movecost 30``  ``/movecost 30 55``
        """
        # Parse the combined bonuses argument
        parts = bonuses.split()
        bonus: int = 0
        new_bonus: int | None = None
        try:
            if len(parts) >= 1:
                bonus = int(parts[0])
            if len(parts) >= 2:
                new_bonus = int(parts[1])
        except ValueError:
            await ctx.send("Invalid input. Use `/movecost`, `/movecost 30`, or `/movecost 30 55`.")
            return
        if not self._client:
            await ctx.send("API client not initialized.")
            return
        if hasattr(ctx, "defer"):
            await ctx.defer()

        try:
            prices_resp = await self._client.get("/itemTrading.getPrices")
        except Exception as exc:
            await ctx.send(f"Failed to fetch market prices: {exc}")
            return

        prices = self._unwrap_prices(prices_resp)
        if not prices:
            await ctx.send("Could not parse market prices from API response.")
            return

        concrete_price = float(prices.get("concrete") or prices.get("Concrete") or 0)
        if concrete_price <= 0:
            await ctx.send("Concrete price not found or is zero in market data.")
            return
        move_cost = 5.0 * concrete_price

        pp_items = ["grain", "lead", "iron", "limestone"]
        pp_prices = [float(prices[k]) for k in pp_items if prices.get(k) and float(prices[k]) > 0]
        if not pp_prices:
            await ctx.send("Could not retrieve enough item prices for PP value calculation.")
            return
        avg_pp_value = sum(pp_prices) / len(pp_prices)

        colour = self._embed_colour()

        def _fmt_h(h: float) -> str:
            """Format a duration in hours as e.g. '7h' or '3d5h'."""
            total_h = round(h)
            if total_h < 24:
                return f"{total_h}h"
            d, rem = divmod(total_h, 24)
            return f"{d}d{rem}h"

        # ── ANSI colour codes (Discord ansi code block) ──────────────────
        G = "\u001b[32m"   # green  — ≤ 3 d
        Y = "\u001b[33m"   # yellow — 3–5 d
        R = "\u001b[31m"   # red    — > 5 d / ∞
        RESET = "\u001b[0m"

        def _col(h: float) -> str:
            return G if h <= 72 else (Y if h <= 120 else R)

        levels = list(range(1, 8))  # engine level 1 … 7

        # ── Single break-even result (new_bonus provided) ─────────────────
        if new_bonus is not None:
            bonus_gain = new_bonus - bonus
            assumption = (
                f"Moving from **{bonus}%** → **{new_bonus}%** (gain: **+{bonus_gain}%**)"
            )
            if bonus_gain <= 0:
                embed = discord.Embed(
                    title="Break-even time — company relocation",
                    description=(
                        f"{assumption}\n\n"
                        f"The new bonus is not higher than your current bonus — move gives no gain."
                    ),
                    colour=colour,
                )
            else:
                level_lines = []
                for lv in levels:
                    extra_per_hour = lv * (bonus_gain / 100) * avg_pp_value
                    h = move_cost / extra_per_hour
                    level_lines.append(f"Level {lv}: **{_fmt_h(h)}**")
                embed = discord.Embed(
                    title="Break-even time — company relocation",
                    description=(
                        f"Automated Engine production time to recover the move cost.\n"
                        f"{assumption}\n\n"
                        + "\n".join(level_lines)
                        + f"\n\n**Move cost:** 5 × {concrete_price:.2f} = **{move_cost:.2f} coins**\n"
                        f"**PP value avg:** {avg_pp_value:.4f} coins/pp"
                    ),
                    colour=colour,
                )
            await ctx.send(embed=embed)
            return

        # ── Full table ────────────────────────────────────────────────────
        bonuses = list(range(5, 85, 5))   # 5 % … 80 % in steps of 5
        CELL = 6  # visual chars per cell (e.g. "  45h" or "3d5h")

        # "Automated Engine Level" centred over the level columns
        level_cols_width = 6 * len(levels)
        eng_label = "Automated Engine Level"
        pad_left = max(0, (level_cols_width - len(eng_label)) // 2)
        eng_header = " " * 7 + " " * pad_left + eng_label

        hdr = f"{'Bonus':>5} │" + "".join(f" {'Lv'+str(lv):<{CELL}}" for lv in levels)
        sep = "──────┼" + "─" * (6 * len(levels))

        rows = []
        for b in bonuses:
            bonus_gain = b - bonus
            cells = []
            for lv in levels:
                if bonus_gain <= 0:
                    cells.append(f"{R}{'∞':>{CELL}}{RESET}")
                else:
                    extra_per_hour = lv * (bonus_gain / 100) * avg_pp_value
                    h = move_cost / extra_per_hour
                    cells.append(f"{_col(h)}{_fmt_h(h):>{CELL}}{RESET}")
            rows.append(f" {b:>3}% │" + "".join(f" {c}" for c in cells))

        table = (
            "```ansi\n"
            + eng_header + "\n"
            + hdr + "\n"
            + sep + "\n"
            + "\n".join(rows)
            + "\n```"
        )

        if bonus > 0:
            assumption = (
                f"Assumes your current production bonus is **{bonus}%**.\n"
                f"Add a second number to get break-even for a specific target, e.g. `/movecost {bonus} 55`."
            )
        else:
            assumption = (
                "Assumes your company currently has **no production bonus**.\n"
                "You can supply your current bonus as a first number (e.g. `/movecost 30`), "
                "and optionally a target bonus as a second number (e.g. `/movecost 30 55`)."
            )
        embed = discord.Embed(
            title="Break-even time — company relocation",
            description=(
                f"Automated Engine production time to recover the move cost.\n"
                f"{assumption}\n\n"
                f"**Move cost:** 5 × {concrete_price:.2f} = **{move_cost:.2f} coins**\n"
                f"**PP value avg:** {avg_pp_value:.4f} coins/pp"
            ),
            colour=colour,
        )

        if len(table) <= 1990:
            await ctx.send(table)
        else:
            await ctx.send(table[:1990] + "\n```")
        await ctx.send(embed=embed)

    @staticmethod
    def _unwrap_prices(resp) -> dict[str, float]:
        """Extract a {itemCode: price} dict from various API response shapes."""
        def _from_dict(d: dict) -> dict[str, float]:
            out: dict[str, float] = {}
            for k, v in d.items():
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
            return out

        if isinstance(resp, dict):
            # Try result.data first
            data = (resp.get("result") or {}).get("data") if isinstance(resp.get("result"), dict) else None
            if isinstance(data, dict):
                result = _from_dict(data)
                if result:
                    return result
            # Try root-level keys that look like item codes
            candidate = _from_dict(resp)
            if candidate:
                return candidate
            # Try a list under any key
            for v in resp.values():
                if isinstance(v, list):
                    out: dict[str, float] = {}
                    for entry in v:
                        if isinstance(entry, dict):
                            code = entry.get("itemCode") or entry.get("item") or entry.get("code")
                            price = entry.get("price") or entry.get("value")
                            if code and price is not None:
                                try:
                                    out[code] = float(price)
                                except (TypeError, ValueError):
                                    pass
                    if out:
                        return out
        if isinstance(resp, list):
            out = {}
            for entry in resp:
                if isinstance(entry, dict):
                    code = entry.get("itemCode") or entry.get("item") or entry.get("code")
                    price = entry.get("price") or entry.get("value")
                    if code and price is not None:
                        try:
                            out[code] = float(price)
                        except (TypeError, ValueError):
                            pass
            return out
        return {}

    # ------------------------------------------------------------------ #
    # Commands — citizen levels                                            #
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="leveldist", description="Show citizen level distribution for a country (or all).")
    @app_commands.describe(
        country="Country code or name (e.g. NL or Netherlands), or leave blank for all countries.",
        all_levels="Show individual levels instead of buckets of 5",
    )
    async def leveldist(self, ctx: Context, country: str | None = None, all_levels: bool = False):
        """Show the cached level distribution for a country, or all countries if no argument given.

        Accepts a country code or name.
        Usage: ``/leveldist NL``  ``/leveldist Netherlands all_levels:True``  ``/leveldist`` (all)
        Prefix shorthand: ``!leveldist NL all``  (trailing 'all' enables all_levels)
        """
        # Prefix mode may pass all_levels inside the country string; strip it here.
        if country and country.lower().endswith(" all"):
            country = country[:-4].strip() or None
            all_levels = True

        if not self._db:
            await ctx.send("Services not initialized.")
            return

        if hasattr(ctx, 'defer'):
            await ctx.defer()

        country_name = "All countries"
        cid: str | None = None

        if country:
            if not self._client:
                await ctx.send("API client not initialized.")
                return
            country_list = await self._fetch_country_list(ctx)
            if country_list is None:
                return
            target = find_country(country, country_list)
            if target is None:
                sample = ", ".join(sorted(str(c.get("code", "")).upper() for c in country_list[:20]))
                await ctx.send(f"Country `{country}` not found. Sample codes: {sample}…")
                return
            cid = cid_of(target)
            country_name = target.get("name", country)

        try:
            level_counts, last_updated = await self._db.get_level_distribution(cid)
        except Exception as exc:
            await ctx.send(f"Database error: {exc}")
            return

        if not level_counts:
            await ctx.send(
                f"No cached level data for **{country_name}** yet.\n"
                f"Run `/poll_citizens{' ' + country if country else ''}` to build the cache."
            )
            return

        total = sum(level_counts.values())
        colour = self._embed_colour()

        if all_levels:
            # Individual level rows
            max_level = max(level_counts)
            bar_max = max(level_counts.values())
            bar_scale = 20 / bar_max
            header = f"{'Lvl':>4}  {'Count':>6}  Bar"
            sep = "─" * 32
            data_rows = [
                f"{lvl:>4}  {level_counts[lvl]:>6}  {'█' * max(1, round(level_counts[lvl] * bar_scale))}"
                for lvl in range(1, max_level + 1)
                if lvl in level_counts
            ]
        else:
            # Bucket rows of 5 levels
            max_level = max(level_counts)
            buckets: dict[int, int] = {}
            for lvl, cnt in level_counts.items():
                bucket = ((lvl - 1) // 5) * 5 + 1
                buckets[bucket] = buckets.get(bucket, 0) + cnt
            bar_max = max(buckets.values())
            bar_scale = 20 / bar_max
            header = f"{'Levels':<9}  {'Count':>6}  Bar"
            sep = "─" * 34
            data_rows = [
                f"{b:>3}–{min(b+4, max_level):<3}  {buckets[b]:>6}  {'█' * max(1, round(buckets[b] * bar_scale))}"
                for b in sorted(buckets)
            ]

        # Send paginated embeds — chunk by character length, not row count
        EMBED_LIMIT = 3900
        label = "All levels" if all_levels else "5-level buckets"
        footer_text = (
            f"{total} citizens  •  {label}"
            + (f"  •  Updated: {last_updated} UTC" if last_updated else "")
        )

        chunks: list[list[str]] = []
        current: list[str] = []
        for row in data_rows:
            candidate = "\n".join(current + [row])
            if len(f"```\n{header}\n{sep}\n{candidate}\n```") > EMBED_LIMIT and current:
                chunks.append(current)
                current = [row]
            else:
                current.append(row)
        if current:
            chunks.append(current)

        for page_idx, chunk in enumerate(chunks):
            block = f"```\n{header}\n{sep}\n" + "\n".join(chunk) + "\n```"
            embed = discord.Embed(
                title=f"Level distribution — {country_name}",
                description=block,
                colour=colour,
            )
            embed.set_footer(text=(
                footer_text if page_idx == 0
                else f"{total} citizens  •  {label} (cont.)"
            ))
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="skilldist", description="Show eco vs war skill distribution for a country (or all).")
    @app_commands.describe(country="Country code or name, or leave blank for all countries combined.")
    async def skilldist(self, ctx: Context, country: str | None = None):
        """Show eco vs war distribution per 5-level bucket, followed by the overall totals.

        A citizen is classified as eco when most of their skill points are in
        entrepreneurship, energy, production, companies, or management.
        All other skills (attack, health, hunger, etc.) count as war skills.
        Ties go to eco.

        Usage: ``/skills NL``  or  ``/skills`` (all countries)
        """
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        if hasattr(ctx, "defer"):
            await ctx.defer()

        country_name = "All countries"
        cid: str | None = None

        if country:
            if not self._client:
                await ctx.send("API client not initialized.")
                return
            country_list = await self._fetch_country_list(ctx)
            if country_list is None:
                return
            target = find_country(country, country_list)
            if target is None:
                sample = ", ".join(sorted(str(c.get("code", "")).upper() for c in country_list[:20]))
                await ctx.send(f"Country `{country}` not found. Sample codes: {sample}…")
                return
            cid = cid_of(target)
            country_name = target.get("name", country)

        try:
            buckets, last_updated = await self._db.get_skill_mode_by_level_buckets(cid)
        except Exception as exc:
            await ctx.send(f"Database error: {exc}")
            return

        if not buckets:
            msg = (
                f"No citizen skill data cached for **{country_name}** yet.\n"
                f"Run `/poll_citizens{' ' + country if country else ''}` to build the cache."
            )
            await ctx.send(msg)
            return

        max_bucket = max(buckets)
        total_eco = sum(v["eco"] for v in buckets.values())
        total_war = sum(v["war"] for v in buckets.values())
        total_unknown = sum(v["unknown"] for v in buckets.values())
        total = total_eco + total_war + total_unknown

        # ── Per-bucket table ──────────────────────────────────────────────
        BAR_W = 12
        header = f"{'Levels':<9}  {'Eco':>5}  {'War':>5}  {'%Eco':>5}  Distribution"
        sep = "─" * (9 + 2 + 5 + 2 + 5 + 2 + 5 + 2 + BAR_W + 4)

        data_rows: list[str] = []
        for b in sorted(buckets):
            bl = buckets[b]
            eco_n = bl["eco"]
            war_n = bl["war"]
            known = eco_n + war_n
            eco_pct = eco_n / known * 100 if known else 0.0
            filled = round(eco_n / known * BAR_W) if known else 0
            bar = "█" * filled + "░" * (BAR_W - filled)
            b_end = min(b + 4, max_bucket + 4)
            data_rows.append(
                f" {b:>3}–{b_end:<3}  {eco_n:>5}  {war_n:>5}  {eco_pct:>4.0f}%  {bar}"
            )

        colour = self._embed_colour()
        EMBED_LIMIT = 3900
        chunks: list[list[str]] = []
        current: list[str] = []
        for row in data_rows:
            candidate = "\n".join(current + [row])
            if len(f"```\n{header}\n{sep}\n{candidate}\n```") > EMBED_LIMIT and current:
                chunks.append(current)
                current = [row]
            else:
                current.append(row)
        if current:
            chunks.append(current)

        footer_parts = [f"{total} citizens total"]
        if total_unknown > 0:
            footer_parts.append(f"{total_unknown} without skill data")
        if last_updated:
            footer_parts.append(f"Updated: {last_updated[:10]} UTC")
        footer_text = "  •  ".join(footer_parts)

        # Build all page embeds first so we can attach the summary to the last one
        total_known = total_eco + total_war

        def _bar(n: int, total_n: int, width: int = 20) -> str:
            filled = round(n / total_n * width) if total_n > 0 else 0
            return "█" * filled + "░" * (width - filled)

        page_embeds: list[discord.Embed] = []
        for page_idx, chunk in enumerate(chunks):
            block = f"```\n{header}\n{sep}\n" + "\n".join(chunk) + "\n```"
            embed = discord.Embed(
                title=f"Skill distribution — {country_name}",
                description=block,
                colour=colour,
            )
            embed.set_footer(text=(
                footer_text if page_idx == 0
                else f"{total} citizens (cont.)"
            ))
            page_embeds.append(embed)

        # Add overall totals as fields on the last page embed
        last_embed = page_embeds[-1]
        if total_known > 0:
            eco_pct_total = total_eco / total_known * 100
            war_pct_total = total_war / total_known * 100
            last_embed.add_field(
                name="🌾 Eco mode",
                value=f"**{total_eco}** ({eco_pct_total:.1f}%)\n`{_bar(total_eco, total_known)}`",
                inline=True,
            )
            last_embed.add_field(
                name="⚔️ War mode",
                value=f"**{total_war}** ({war_pct_total:.1f}%)\n`{_bar(total_war, total_known)}`",
                inline=True,
            )

        for embed in page_embeds:
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="skillcooldown", description="Show skill-reset cooldown stats per 5-level bucket for a country (or all).")
    @app_commands.describe(country="Country code or name, or leave blank for all countries combined.")
    async def skillcooldown(self, ctx: Context, country: str | None = None):
        """Show average days since last skill reset per 5-level bucket.

        A player can reset skills once every 7 days.  This command shows,
        per level group, how long ago citizens last reset and how many can
        already reset again.

        Usage: ``/skillcooldown NL``  or  ``/skillcooldown`` (all countries)
        """
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        if hasattr(ctx, "defer"):
            await ctx.defer()

        country_name = "All countries"
        cid: str | None = None

        if country:
            if not self._client:
                await ctx.send("API client not initialized.")
                return
            country_list = await self._fetch_country_list(ctx)
            if country_list is None:
                return
            target = find_country(country, country_list)
            if target is None:
                sample = ", ".join(sorted(str(c.get("code", "")).upper() for c in country_list[:20]))
                await ctx.send(f"Country `{country}` not found. Sample codes: {sample}\u2026")
                return
            cid = cid_of(target)
            country_name = target.get("name", country)

        try:
            buckets, last_updated = await self._db.get_skill_reset_cooldown_by_level_buckets(cid)
        except Exception as exc:
            await ctx.send(f"Database error: {exc}")
            return

        if not buckets:
            await ctx.send(
                f"No citizen skill-reset data cached for **{country_name}** yet.\n"
                f"Run `/poll_citizens{' ' + country if country else ''}` to build the cache."
            )
            return

        max_bucket = max(buckets)
        total_with_data = sum(v["count"] for v in buckets.values())
        total_available = sum(v["available"] for v in buckets.values())
        total_no_data = sum(v["no_data"] for v in buckets.values())
        total_citizens = total_with_data + total_no_data

        COOLDOWN_DAYS = 7
        BAR_W = 10  # bar represents 0–7 days available to reset (0 d = full, 7+ d = empty wait)

        header = f"{'Levels':<9}  {'Citizens':>8}  {'Since reset':>11}  {'Can reset':>9}  Cooldown"
        sep = "\u2500" * (9 + 2 + 8 + 2 + 11 + 2 + 9 + 2 + BAR_W)

        data_rows: list[str] = []
        for b in sorted(buckets):
            bv = buckets[b]
            total_b = bv["count"] + bv["no_data"]
            avg_days = bv["avg_days_ago"]
            avail = bv["available"]
            avail_pct = avail / total_b * 100 if total_b else 0.0
            b_end = min(b + 4, max_bucket + 4)
            # bar shows avg cooldown remaining (days left until can reset)
            # no data → show dashes so it's visually distinct
            if bv["count"] == 0:
                bar = "\u2500" * BAR_W
            else:
                avg_remaining = max(0.0, COOLDOWN_DAYS - avg_days)
                filled = round(avg_remaining / COOLDOWN_DAYS * BAR_W)
                bar = "\u2588" * filled + "\u2591" * (BAR_W - filled)
            avg_str = f"{avg_days:.1f}d" if bv["count"] else "n/a"
            data_rows.append(
                f" {b:>3}\u2013{b_end:<3}  {total_b:>8}  {avg_str:>11}  {avail:>5} {avail_pct:>3.0f}%  {bar}"
            )

        colour = self._embed_colour()
        EMBED_LIMIT = 3900
        chunks: list[list[str]] = []
        current: list[str] = []
        for row in data_rows:
            candidate = "\n".join(current + [row])
            if len(f"```\n{header}\n{sep}\n{candidate}\n```") > EMBED_LIMIT and current:
                chunks.append(current)
                current = [row]
            else:
                current.append(row)
        if current:
            chunks.append(current)

        footer_parts = [f"{total_citizens} citizens total"]
        if last_updated:
            footer_parts.append(f"Updated: {last_updated[:10]} UTC")
        footer_text = "  \u2022  ".join(footer_parts)

        page_embeds: list[discord.Embed] = []
        for page_idx, chunk in enumerate(chunks):
            block = f"```\n{header}\n{sep}\n" + "\n".join(chunk) + "\n```"
            embed = discord.Embed(
                title=f"Skill-reset cooldown \u2014 {country_name}",
                description=block,
                colour=colour,
            )
            embed.set_footer(text=(
                footer_text if page_idx == 0
                else f"{total_citizens} citizens (cont.)"
            ))
            page_embeds.append(embed)

        # Overall summary on last embed
        last_embed = page_embeds[-1]
        if total_citizens > 0:
            avail_pct_total = total_available / total_citizens * 100
            if total_with_data > 0:
                overall_avg = sum(v["avg_days_ago"] * v["count"] for v in buckets.values()) / total_with_data
                last_embed.add_field(
                    name="\u23f1\ufe0f Avg days since reset",
                    value=f"**{overall_avg:.1f}** days",
                    inline=True,
                )
            last_embed.add_field(
                name="\u2705 Can reset now",
                value=f"**{total_available}** ({avail_pct_total:.0f}%)",
                inline=True,
            )

        for embed in page_embeds:
            await ctx.send(embed=embed)

    @commands.hybrid_command(name="poll_citizens", description="Refresh citizen level cache.")
    @app_commands.describe(country="Country code or name, or leave blank for all countries")
    @_has_privileged_role()
    async def poll_citizens(self, ctx: Context, country: str | None = None):
        """Refresh the citizen level cache for one country, or all countries if no argument given.

        Usage: ``/poll_citizens NL``  or  ``/poll_citizens`` (all)
        """
        if not self._client or not self._db or not self._citizen_cache:
            await ctx.send("Services not initialized.")
            return

        if hasattr(ctx, 'defer'):
            await ctx.defer()
        country_list = await self._fetch_country_list(ctx)
        if country_list is None:
            return

        if country:
            target = find_country(country, country_list)
            if target is None:
                await ctx.send(f"Country `{country}` not found.")
                return
            countries = [target]
        else:
            countries = country_list

        n = len(countries)
        label = f"**{countries[0].get('name', country)}**" if n == 1 else f"**{n}** countries"
        status_msg = await ctx.send(f"Starting citizen level refresh for {label}…")

        import time
        t_start = time.monotonic()
        total_recorded = 0
        failed: list[str] = []
        for i, c in enumerate(countries, 1):
            cid = cid_of(c)
            name = c.get("name", cid)
            if n > 1:
                await status_msg.edit(content=f"Refreshing citizen levels… ({i}/{n}) **{name}**")
            try:
                recorded = await self._citizen_cache.refresh_country(
                    cid, name,
                    progress_msg=status_msg if n == 1 else None,
                )
                total_recorded += recorded
                self.bot.logger.info("poll_citizens: %s — %d levels cached", name, recorded)
            except Exception:
                self.bot.logger.exception("poll_citizens: error for %s", name)
                failed.append(name)

        elapsed = time.monotonic() - t_start
        elapsed_str = (
            f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
            if elapsed >= 60
            else f"{elapsed:.1f}s"
        )
        if n == 1:
            summary = f"Citizen level cache refreshed for **{countries[0].get('name', country)}** — {total_recorded} levels stored. ⏱ {elapsed_str}"
        else:
            summary = f"Citizen level cache refreshed for **{n}** countries — {total_recorded} levels stored. ⏱ {elapsed_str}"
        if failed:
            summary += f"\nFailed: {', '.join(failed)}"
        await status_msg.edit(content=summary)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _fetch_country_list(self, ctx: Context) -> list[dict] | None:
        """Fetch and unwrap the country list; sends an error to ctx on failure."""
        try:
            resp = await self._client.get("/country.getAllCountries")
        except Exception as exc:
            await ctx.send(f"Failed to fetch countries: {exc}")
            return None
        result = extract_country_list(resp)
        if not result:
            await ctx.send("Could not retrieve country list from API.")
            return None
        return result


async def setup(bot) -> None:
    await bot.add_cog(ProductionChecker(bot))

