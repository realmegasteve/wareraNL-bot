"""Geluk cog — /geluk command to analyse a player's case-opening luck."""

import json
import logging
import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.api_client import APIClient

logger = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# Expected drop rates per rarity (from the game's stated probabilities)
# ---------------------------------------------------------------------------
RARITY_ORDER = ["mythic", "legendary", "epic", "rare", "uncommon", "common"]

EXPECTED_RATES: dict[str, float] = {
    "mythic":    0.0001,   # 0.01 %
    "legendary": 0.0004,   # 0.04 %
    "epic":      0.0085,   # 0.85 %
    "rare":      0.071,    # 7.1  %
    "uncommon":  0.30,     # 30   %
    "common":    0.62,     # 62   %
}

# Display labels (in Dutch / in-game naming)
RARITY_LABELS: dict[str, str] = {
    "mythic":    "Mythisch",
    "legendary": "Legendarisch",
    "epic":      "Episch",
    "rare":      "Zeldzaam",
    "uncommon":  "Ongewoon",
    "common":    "Gewoon",
}

RARITY_COLORS: dict[str, str] = {
    "mythic":    "🔴",
    "legendary": "🟠",
    "epic":      "🟣",
    "rare":      "🔵",
    "uncommon":  "🟢",
    "common":    "⚪",
}


def _unwrap(resp: dict) -> dict:
    if isinstance(resp, dict):
        return resp.get("result", {}).get("data", resp)
    return resp


def _luck_indicator(actual_rate: float, expected_rate: float) -> str:
    """Return a luck emoji based on how far actual deviates from expected."""
    if expected_rate == 0:
        return ""
    ratio = actual_rate / expected_rate
    if ratio >= 1.5:
        return "🍀🍀"
    if ratio >= 1.2:
        return "🍀"
    if ratio >= 0.8:
        return "➖"
    if ratio >= 0.5:
        return "💀"
    return "💀💀"


def _build_luck_table(
    total: int,
    counts: dict[str, int],
) -> str:
    """Build a compact fixed-width text table comparing actual vs expected drops."""
    header = f"{'Zeldzaamheid':<14} {'Vrw':>6} {'Gkr':>5}  {'Jij%':>6}  Geluk"
    sep = "─" * len(header)
    rows = [header, sep]
    for rarity in RARITY_ORDER:
        expected_rate = EXPECTED_RATES[rarity]
        expected_n = total * expected_rate
        actual_n = counts.get(rarity, 0)
        actual_rate = actual_n / total if total > 0 else 0.0
        luck = _luck_indicator(actual_rate, expected_rate)
        label = RARITY_LABELS[rarity]
        rows.append(
            f"{label:<14} {expected_n:>6.1f} {actual_n:>5d}  {actual_rate*100:>5.2f}%  {luck}"
        )
    rows.append(sep)
    rows.append(f"{'Totaal':<14} {total:>6d} {sum(counts.values()):>5d}")
    return "\n".join(rows)


class Geluk(commands.Cog, name="geluk"):
    """Player case-opening luck analyser."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: dict = getattr(bot, "config", {}) or {}
        self._client: Optional[APIClient] = None
        self._item_rarity_cache: dict[str, str] = {}  # itemCode → rarity

    async def _get_client(self) -> APIClient:
        if self._client is None:
            base_url = self.config.get("api_base_url", "https://api2.warera.io/trpc")
            api_keys = None
            try:
                with open("_api_keys.json") as f:
                    api_keys = json.load(f).get("keys", [])
            except FileNotFoundError:
                pass
            self._client = APIClient(base_url=base_url, api_keys=api_keys)
            await self._client.start()
        return self._client

    async def _get_item_rarities(self) -> dict[str, str]:
        """Load item code → rarity mapping from gameConfig (cached)."""
        if self._item_rarity_cache:
            return self._item_rarity_cache
        try:
            client = await self._get_client()
            raw = await client.get("/gameConfig.getGameConfig", params={"input": "{}"})
            data = _unwrap(raw)
            items: dict = data.get("items", {}) if isinstance(data, dict) else {}
            for code, item in items.items():
                rarity = item.get("rarity")
                if rarity:
                    self._item_rarity_cache[code] = rarity
            logger.info("Geluk: loaded %d item rarities from gameConfig", len(self._item_rarity_cache))
        except Exception as exc:
            logger.warning("Geluk: could not load item rarities: %s", exc)
        return self._item_rarity_cache

    async def _search_user(self, username: str) -> Optional[str]:
        """Search for a player by username and return their user ID, or None."""
        client = await self._get_client()
        try:
            raw = await client.get(
                "/search.searchAnything",
                params={"input": json.dumps({"searchText": username})},
            )
            data = _unwrap(raw)
            user_ids: list = data.get("userIds", []) if isinstance(data, dict) else []
            return user_ids[0] if user_ids else None
        except Exception as exc:
            logger.warning("Geluk: search failed for %r: %s", username, exc)
            return None

    async def _get_user_profile(self, user_id: str) -> Optional[dict]:
        """Return getUserLite data for a user."""
        client = await self._get_client()
        try:
            raw = await client.get(
                "/user.getUserLite",
                params={"input": json.dumps({"userId": user_id})},
            )
            return _unwrap(raw) if isinstance(raw, dict) else None
        except Exception as exc:
            logger.warning("Geluk: getUserLite failed for %s: %s", user_id, exc)
            return None

    async def _fetch_all_case_transactions(
        self,
        user_id: str,
        item_rarities: dict[str, str],
    ) -> Optional[dict[str, int]]:
        """
        Page through all openCase transactions for a user.

        Returns a dict of {rarity: count}, or None if the endpoint is
        inaccessible (auth error).
        """
        client = await self._get_client()
        counts: dict[str, int] = {r: 0 for r in RARITY_ORDER}
        cursor: Optional[str] = None
        page = 0
        total_fetched = 0

        while True:
            payload: dict = {
                "userId": user_id,
                "transactionType": "openCase",
                "limit": 100,
            }
            if cursor:
                payload["cursor"] = cursor

            try:
                raw = await client.get(
                    "/transaction.getPaginatedTransactions",
                    params={"input": json.dumps(payload)},
                )
            except Exception as exc:
                err = str(exc)
                if "401" in err or "Unauthorized" in err:
                    logger.info(
                        "Geluk: transaction endpoint requires session auth — "
                        "cannot retrieve case history for %s", user_id
                    )
                    return None
                logger.warning("Geluk: transaction fetch error page %d: %s", page, exc)
                break

            data = _unwrap(raw) if isinstance(raw, dict) else {}
            items = []
            if isinstance(data, dict):
                items = data.get("items") or data.get("transactions") or data.get("results") or []
                cursor = data.get("nextCursor") or data.get("cursor")
            elif isinstance(data, list):
                items = data
                cursor = None

            for tx in items:
                if not isinstance(tx, dict):
                    continue
                # Skip elite cases (case2, mythic rarity) — the profile ranking
                # only counts regular case1 openings, so we do the same.
                opened_case = tx.get("itemCode", "")
                if item_rarities.get(opened_case) == "mythic":
                    continue
                # "itemCode" is the *case* that was opened; the *received* drop is in item.code
                received_item = tx.get("item") or {}
                item_code = (
                    received_item.get("code") if isinstance(received_item, dict) else received_item
                ) or ""
                rarity = item_rarities.get(item_code, "common")
                counts[rarity] = counts.get(rarity, 0) + 1

            total_fetched += len(items)
            page += 1

            if not cursor or not items:
                break

            await asyncio.sleep(0.3)

        logger.info(
            "Geluk: fetched %d case transactions for %s across %d pages",
            total_fetched, user_id, page,
        )
        return counts

    # ------------------------------------------------------------------
    # Slash command
    # ------------------------------------------------------------------

    @app_commands.command(
        name="geluk",
        description="Controleer het geluk van een speler bij het openen van cases",
    )
    @app_commands.describe(speler="De gebruikersnaam van de speler om te controleren")
    async def geluk(self, interaction: discord.Interaction, speler: str) -> None:
        await interaction.response.defer(thinking=True)

        # 1. Find player
        user_id = await self._search_user(speler)
        if not user_id:
            await interaction.followup.send(
                f"❌ Speler **{discord.utils.escape_markdown(speler)}** niet gevonden.",
                ephemeral=True,
            )
            return

        # 2. Get profile
        profile = await self._get_user_profile(user_id)
        if not profile:
            await interaction.followup.send(
                "❌ Kon het profiel van de speler niet ophalen.",
                ephemeral=True,
            )
            return

        username: str = profile.get("username") or speler
        avatar_url: str = profile.get("avatarUrl") or ""
        rankings: dict = profile.get("rankings") or {}
        cases_ranking: dict = rankings.get("userCasesOpened") or {}
        total_cases_opened: int = int(cases_ranking.get("value") or 0)
        cases_rank: Optional[int] = cases_ranking.get("rank")

        # 3. Load item rarities from game config
        item_rarities = await self._get_item_rarities()

        # 4. Try to fetch actual transaction history
        counts = await self._fetch_all_case_transactions(user_id, item_rarities)
        can_show_actual = counts is not None

        # 5. Build embed
        embed = discord.Embed(
            title=f"🎰 Case-geluk van {username}",
            color=discord.Color.gold(),
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        # Cases opened summary line
        rank_str = f" (rank #{cases_rank})" if cases_rank else ""
        embed.add_field(
            name="Cases geopend",
            value=f"**{total_cases_opened:,}**{rank_str}",
            inline=False,
        )

        if not can_show_actual:
            # Transaction API not accessible — show expected distribution only
            embed.description = (
                "⚠️ De transactie-API vereist inloggegevens van de speler zelf — "
                "individuele drops zijn niet beschikbaar via de publieke API.\n\n"
                "Hieronder staat de **verwachte** verdeling op basis van het totaal aantal "
                "geopende cases."
            )
            if total_cases_opened > 0:
                lines = ["```"]
                lines.append(f"{'Zeldzaamheid':<14} {'Verwacht':>8}  {'Kans%':>6}")
                lines.append("─" * 34)
                for rarity in RARITY_ORDER:
                    rate = EXPECTED_RATES[rarity]
                    expected_n = total_cases_opened * rate
                    label = RARITY_LABELS[rarity]
                    lines.append(f"{label:<14} {expected_n:>8.1f}  {rate*100:>5.2f}%")
                lines.append("─" * 34)
                lines.append(f"{'Totaal':<14} {total_cases_opened:>8,}")
                lines.append("```")
                embed.add_field(
                    name="Verwachte verdeling",
                    value="\n".join(lines),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Geen cases gevonden",
                    value="Deze speler heeft nog geen cases geopend.",
                    inline=False,
                )
        else:
            # We have actual data
            total_counted = sum(counts.values())

            if total_counted == 0:
                embed.description = "Deze speler heeft nog geen cases geopend (of er waren geen geregistreerde drops)."
            else:
                analysed_note = f"_{total_counted:,} case openings gevonden_"
                table = _build_luck_table(total_counted, counts)
                embed.add_field(name="Geluksanalyse", value=f"{analysed_note}\n```\n{table}\n```", inline=False)

        embed.set_footer(text="Kansen: mythisch 0.01% • legendarisch 0.04% • episch 0.85% • zeldzaam 7.1% • ongewoon 30% • gewoon 62%")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Geluk(bot))
