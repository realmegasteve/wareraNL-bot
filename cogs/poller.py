"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""

import os
import json
import logging
from datetime import datetime
from discord.ext import commands, tasks
from discord.ext.commands import Context
import asyncio

# local services (lightweight skeletons)
from services.api_client import APIClient
from services.db import Database

CONFIG_FILE = "config.json"

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            return config
        
def save_config(config: dict) -> None:
    """Save configuration to JSON file with pretty formatting."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

logger = logging.getLogger("discord_bot")

# Here we name the cog and create a new class for the cog.
class ProductionChecker(commands.Cog, name="production_checker"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.config = load_config()
        self._client: APIClient | None = None
        self._db: Database | None = None
        self._poll_lock: asyncio.Lock = asyncio.Lock()

    # Here you can just add your own commands, you'll always need to provide "self" as first parameter.

    def cog_load(self) -> None:
        """Start the scheduled tasks when the cog is loaded."""
        # Initialize services lazily
        asyncio.create_task(self._ensure_services_and_start())

    def cog_unload(self) -> None:
        """Cancel scheduled tasks when the cog is unloaded."""
        self.hourly_production_check.cancel()
        # close services if initialized
        if self._client:
            asyncio.create_task(self._client.close())
        if self._db:
            asyncio.create_task(self._db.close())

    async def _ensure_services_and_start(self) -> None:
        # Create API client and DB using config values (or defaults)
        base_url = self.config.get("api_base_url", "https://api.example.local")
        db_path = self.config.get("external_db_path", "database/external.db")
        # load api keys if available
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
        self.hourly_production_check.start()

    @tasks.loop(minutes=15)  # Runs every hour
    async def hourly_production_check(self):
        """Scheduled wrapper that ensures only one poll runs at a time."""
        async with self._poll_lock:
            await self._run_poll_once()

    async def _run_poll_once(self) -> None:
        """Perform a single production poll (extracted from the scheduled task).
        This can be called from the scheduled loop or from a manual command.

        Returns a list of change tuples: (item, prev_desc, new_desc).
        """
        self.bot.logger.info("Starting production poll...")
        try:
            # Get the production channel
            market_channel_id = self.config.get("production_channel_id")
            if not market_channel_id:
                self.bot.logger.warning("Market channel ID not configured")
                return

            # fetch latest state from DB
            last_seen = None
            if self._db:
                last_seen = await self._db.get_poll_state("production_checker_last_seen")

            # perform country polling: get all countries and check each one
            if not self._client:
                self.bot.logger.warning("API client not initialized")
                return

            if not self.config.get("api_base_url"):
                self.bot.logger.warning("api_base_url not configured; skipping country polling")
                return

            try:
                all_countries = await self._client.get("/country.getAllCountries")
            except Exception:
                self.bot.logger.exception("Failed to fetch country list")
                return

            # extract list of country objects from the response
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

            if not country_list:
                self.bot.logger.info("No country objects found in /country.getAllCountries response")
                return

            # build current top per specialization
            tops: dict[str, dict] = {}
            now = datetime.utcnow().isoformat() + "Z"

            def _get_production_bonus(obj):
                # first try rankings.countryProductionBonus.value
                try:
                    rb = obj.get("rankings", {}).get("countryProductionBonus")
                    if isinstance(rb, dict) and "value" in rb:
                        return float(rb.get("value"))
                except Exception:
                    pass
                # fallback to strategicResources.bonuses.productionPercent
                try:
                    sp = obj.get("strategicResources", {}).get("bonuses", {}).get("productionPercent")
                    if sp is not None:
                        return float(sp)
                except Exception:
                    pass
                return None

            for country in country_list:
                cid = country.get("_id") or country.get("id") or country.get("countryId") or country.get("code")
                code = country.get("code")
                name = country.get("name")
                specialized_item = country.get("specializedItem") or country.get("specialized_item") or country.get("specialization")
                pb = _get_production_bonus(country)

                # Apply ruling party "industrialism" ethics if present.
                adjusted_pb = None
                rp_id = None
                for key in ("rulingParty", "ruling_party", "rulingPartyId", "ruling_party_id"):
                    if key in country:
                        rp = country[key]
                        if rp is None:
                            break
                        if isinstance(rp, dict):
                            for k2 in ("id", "partyId", "party_id"):
                                if k2 in rp and rp[k2] is not None:
                                    rp_id = str(rp[k2])
                                    break
                        else:
                            # ensure we don't convert None to the string "None"
                            if rp:
                                rp_id = str(rp)
                        break

                if rp_id and pb is not None and self._client:
                    try:
                        party = await self._client.get("/party.getById", params={"input": json.dumps({"partyId": rp_id})})
                        industrial_level = None
                        ethics_obj = None
                        if isinstance(party, dict):
                            obj = party.get("result") if isinstance(party.get("result"), dict) else None
                            if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
                                obj = obj.get("data")
                            else:
                                obj = party.get("data") if isinstance(party.get("data"), dict) else obj
                            if isinstance(obj, dict) and "ethics" in obj:
                                ethics_obj = obj.get("ethics")
                            elif "ethics" in party:
                                ethics_obj = party.get("ethics")
                        if isinstance(ethics_obj, dict):
                            industrial_level = ethics_obj.get("industrialism")
                            if industrial_level is None:
                                industrial_level = ethics_obj.get("industrial")
                        # industrialism adds percentage points (e.g. +30 means 33 -> 63)
                        extra_points = 0.0
                        if industrial_level == 1:
                            extra_points = 10.0
                        elif industrial_level == 2:
                            extra_points = 30.0
                        try:
                            pb_num = float(pb)
                            adjusted_pb = pb_num + extra_points
                            pb = adjusted_pb
                            self.bot.logger.debug("Country %s ruling party %s industrialism=%s -> +%spp", cid, rp_id, industrial_level, int(extra_points))
                        except Exception:
                            pass
                    except Exception:
                        self.bot.logger.exception("Failed to fetch party %s", rp_id)

                # persist snapshot
                if self._db and cid:
                    try:
                        await self._db.save_country_snapshot(str(cid), code, name, specialized_item, pb, json.dumps(country, default=str), now)
                    except Exception:
                        self.bot.logger.exception("Failed to save snapshot for country %s", cid)

                if not specialized_item:
                    continue

                current = tops.get(specialized_item)
                # choose highest production bonus; treat None as -inf
                cur_bonus = current.get("production_bonus") if current else None
                if cur_bonus is None:
                    cur_bonus_val = float("-inf")
                else:
                    cur_bonus_val = cur_bonus

                this_bonus = pb if pb is not None else float("-inf")
                if this_bonus > cur_bonus_val:
                    tops[specialized_item] = {"country_id": str(cid), "country_name": name, "production_bonus": pb}

            # compare with previous tops and notify on changes
            changes: list[tuple[str, str, str]] = []
            for item, top in tops.items():
                try:
                    prev = await self._db.get_top_specialization(item) if self._db else None
                except Exception:
                    prev = None

                changed = False
                prev_desc = None
                if prev is None:
                    changed = True
                    prev_desc = "(none)"
                else:
                    # Only treat it as a change when the country owning the top specialization changes.
                    # Changes to the production bonus for the same country should not trigger notifications.
                    if prev.get("country_id") != top.get("country_id") and prev.get("production_bonus") != top.get("production_bonus"):
                        changed = True
                        prev_desc = f"{prev.get('country_name')} with bonus {prev.get('production_bonus')}%"

                if changed:
                    # send message to configured channel in every guild
                    for guild in self.bot.guilds:
                        channel = guild.get_channel(market_channel_id)
                        if channel:
                            old = prev_desc or "(none)"
                            new = f"{top.get('country_name')} with bonus {top.get('production_bonus')}%"
                            text = f"Specialization '{item}' new leader: {new} replacing {old}"
                            try:
                                await channel.send(text)
                            except Exception:
                                self.bot.logger.exception("Failed sending specialization update for item %s to guild %s", item, guild.name)

                    # record change for caller reporting
                    new_desc = f"{top.get('country_name')} ({top.get('country_id')}) bonus={top.get('production_bonus')}"
                    changes.append((item, prev_desc or "(none)", new_desc))

                # persist new top
                if self._db:
                    try:
                        await self._db.set_top_specialization(item, top.get("country_id"), top.get("country_name"), float(top.get("production_bonus") or 0), now)
                    except Exception:
                        self.bot.logger.exception("Failed to persist top specialization for %s", item)
        except Exception as e:
            self.bot.logger.error(f"Error sending hourly production check: {e}")
            return []

        return changes

    @commands.command(name="poll_now")
    @commands.is_owner()
    async def poll_now(self, ctx: Context):
        """Owner-only command to trigger a single production poll."""
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

            # craft a concise report
            lines = [f"Production poll completed — {len(changes)} change(s):"]
            for item, prev, new in changes:
                lines.append(f"• {item}: {new} (replaced {prev})")

            # send report in chunks if necessary
            try:
                await channel.send("\n".join(lines))
            except Exception:
                self.bot.logger.exception("Failed to send poll report")

        # run in background to avoid blocking the command
        asyncio.create_task(_run_and_report())

    @commands.command(name="simulate_prev_top")
    @commands.is_owner()
    async def simulate_prev_top(self, ctx: Context, item: str, country_id: str, country_name: str, bonus: float):
        """Insert or replace a previous top for `item` to simulate a future change.

        Usage: `!simulate_prev_top cookedFish fakeid Oldland 10.0`
        """
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        now = datetime.utcnow().isoformat() + "Z"
        try:
            await self._db.set_top_specialization(item, country_id, country_name, float(bonus), now)
            await ctx.send(f"Simulated previous top for '{item}' -> {country_name} ({country_id}) bonus={bonus}")
        except Exception:
            self.bot.logger.exception("Failed to simulate previous top")
            await ctx.send("Failed to simulate previous top; see logs.")


    @commands.command(name="leaders")
    async def leaders(self, ctx: Context):
        """Display the current production leaders for each specialization."""
        if not self._db:
            await ctx.send("Database not initialized.")
            return
        try:
            tops = await self._db.get_all_tops()
        except Exception:
            self.bot.logger.exception("Failed to fetch production leaders")
            await ctx.send("Failed to fetch production leaders; see logs.")
            return

        if not tops:
            await ctx.send("No production leaders recorded.")
            return

        lines = []
        for t in tops:
            pb = t.get("production_bonus")
            pb_text = f" bonus={pb}" if pb is not None else ""
            lines.append(f"{t.get('item')}: {t.get('country_name')} ({t.get('country_id')}){pb_text}")

        # Send in a single message if small, otherwise chunk
        message = "\n".join(lines)
        try:
            await ctx.send(message)
        except Exception:
            self.bot.logger.exception("Failed to send production leaders message")


    @hourly_production_check.before_loop
    async def before_hourly_production_check(self):
        """Ensure the bot is ready before starting the scheduled task."""
        await self.bot.wait_until_ready()



# And then we finally add the cog to the bot so that it can load, unload, reload and use it's content.
async def setup(bot) -> None:
    await bot.add_cog(ProductionChecker(bot))
