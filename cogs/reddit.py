"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""

import asyncio
import json
import os
from pathlib import Path
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
import datetime
import logging
import requests

DEFAULT_HEADERS = {
    "User-Agent": "WareraNLBot/1.0 (by /u/Creepino +https://github.com/colgre/wareraNL-bot)"
}


logger = logging.getLogger("discord_bot")


class RedditTracker(commands.Cog, name="reddit"):
	"""Cog that polls a subreddit and posts new submissions to a channel.

	Behavior:
	- Polls `/r/<subreddit>/new.json` every `reddit_poll_interval_seconds` (config)
	- Stores last-seen post id in `data/reddit_state.json`
	- Posts new submissions (oldest first) to channel configured in
	  `bot.config['channels']['reddit']` or falls back to `channels.production`.
	"""

	def __init__(self, bot: commands.Bot) -> None:
		self.bot = bot
		self.logger = bot.logger if hasattr(bot, "logger") else logger
		self.subreddit = "WarEraNL"
		self.interval = 600 # 10 minutes
		self.state_path = Path("data") / "reddit_state.json"
		self.session = aiohttp.ClientSession(trust_env=True, headers=DEFAULT_HEADERS, version=aiohttp.HttpVersion11)
		self._last_seen: str | None = None
		self.poll_task = tasks.loop(seconds=self.interval)(self._poll)

	async def cog_load(self) -> None:
		# Ensure data directory exists and load last seen id
		self.state_path.parent.mkdir(parents=True, exist_ok=True)
		if self.state_path.exists():
			try:
				with self.state_path.open("r", encoding="utf-8") as f:
					data = json.load(f)
					self._last_seen = data.get("last_post_id")
					self.logger.debug(f"Loaded reddit state, last_post_id={self._last_seen}")
			except Exception:
				self.logger.exception("Failed to read reddit state file")
		else:
			self._last_seen = None

		self.session = aiohttp.ClientSession(trust_env=True, headers=DEFAULT_HEADERS, version=aiohttp.HttpVersion11)
		# start the polling loop after cog is loaded
		self.poll_task.start()

	async def cog_unload(self) -> None:
		# Cancel the polling task and close session
		try:
			if self.poll_task.is_running():
				self.poll_task.cancel()
		except Exception:
			pass
		if self.session:
			await self.session.close()

	async def _save_state(self) -> None:
		try:
			with self.state_path.open("w", encoding="utf-8") as f:
				json.dump({"last_post_id": self._last_seen}, f)
		except Exception:
			self.logger.exception("Failed to write reddit state file")

	async def _fetch_new(self) -> list[dict]:
		url = f"https://www.reddit.com/r/{self.subreddit}/new.json?limit=5"
		try:
			self.logger.debug("Fetching URL: %s with headers: %s", url, self.session.headers)
			async with self.session.get(url, timeout=15, allow_redirects=True) as resp:
				if resp.status == 403:
					body = await resp.text()
					self.logger.warning("Reddit 403 Forbidden: %s", body)
					return []
				if resp.status != 200:
					self.logger.warning("Reddit status %s; headers=%s", resp.status, dict(resp.headers))
					return []
				payload = await resp.json()
				items = payload.get("data", {}).get("children", [])
				posts = [c.get("data", {}) for c in items]
				return posts
		except asyncio.CancelledError:
			raise
		except Exception as e:
			self.logger.exception("Failed to fetch reddit feed: %s", e)
			return []

	async def _poll(self) -> None:
		posts = await self._fetch_new()
		if not posts:
			return

		# Collect posts newer than last_seen; Reddit returns newest first
		new_posts = []
		for p in posts:
			if self._last_seen is None or p.get("name") != self._last_seen:
				new_posts.append(p)
			else:
				break

		if not new_posts:
			return

		# oldest-first
		new_posts.reverse()

		# Determine channel id
		channels_cfg = self.bot.config.get("channels", {})
		channel_id = channels_cfg.get("logs")
		if not channel_id:
			self.logger.warning("No `channels.reddit` or configured; skipping reddit posts")
			return

		channel = self.bot.get_channel(int(channel_id))
		if channel is None:
			self.logger.warning(f"Configured reddit channel {channel_id} not found")
			return

		for post in new_posts:
			try:
				title = post.get("title")
				author = post.get("author")
				permalink = post.get("permalink")
				url = post.get("url")
				is_self = post.get("is_self", False)
				post_name = post.get("name")

				embed = discord.Embed(title=title, url=f"https://reddit.com{permalink}", color=0xFF4500)
				embed.set_author(name=f"u/{author}")
				created_utc = post.get("created_utc")
				if created_utc:
					embed.timestamp = datetime.datetime.fromtimestamp(created_utc, datetime.timezone.utc)
				if is_self:
					selftext = post.get("selftext", "")
					if selftext:
						description = (selftext[:1900] + "…") if len(selftext) > 1900 else selftext
						embed.description = description
				else:
					# For link/image posts show the URL
					embed.add_field(name="Link", value=url, inline=False)
					if post.get("thumbnail") and post.get("thumbnail", "").startswith("http"):
						embed.set_thumbnail(url=post.get("thumbnail"))

				await channel.send(embed=embed)
				self._last_seen = post_name
				await self._save_state()
				self.logger.info(f"Posted new reddit submission {post_name} -> {channel.id}")
			except Exception:
				self.logger.exception("Failed posting reddit submission")

	@commands.command(name="reddit_poll", help="Force a reddit poll (owner only)")
	@commands.is_owner()
	async def reddit_poll(self, ctx: commands.Context) -> None:
		await ctx.send("Running reddit poll...")
		await self._poll()
		await ctx.send("Done.")


async def setup(bot: commands.Bot) -> None:
	await bot.add_cog(RedditTracker(bot))

async def main():
	import requests
	import httpx

	headers = {'User-Agent': 'WarEraNL-bot/1.0 (by /u/Creepino +https://github.com/colgre/wareraNL-bot)'}

	url = 'https://www.reddit.com/r/WarEraNL.json'

	# Test with requests
	resp = requests.get(url, headers=headers)
	print("Requests Response:", resp.json())

	# Test with aiohttp
	async with aiohttp.ClientSession(trust_env=True, headers=headers, version=aiohttp.HttpVersion11) as session:
		async with session.get(url, timeout=15, allow_redirects=True) as resp:
			print("aiohttp Response Status:", resp.status)
			if resp.status == 200:
				print("aiohttp Response:", await resp.json())

	url = "https://www.reddit.com/r/WarEraNL.json"
	headers = {"User-Agent": "WarEraNL-bot/1.0 (by /u/Creepino +https://github.com/colgre/wareraNL-bot)"}

	async with httpx.AsyncClient(http1=True) as client:
		response = await client.get(url, headers=headers)
		print("HTTPX Response Status:", response.status_code)
		if response.status_code == 200:
			print("HTTPX Response:", response.json())
		# else:
			# print("HTTPX Error Response:", response.text)

            

if __name__ == "__main__":
    asyncio.run(main())