"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""

import json
import os
import discord
from discord import app_commands
from discord.ext import commands


TEMPLATE_PATH = f"{os.path.realpath(os.path.dirname(__file__))}/../../templates/mu_roles.json"


def load_roles_template() -> dict:
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"title": "Choose your roles", "description": "Click a button to toggle roles.", "buttons": []}


def button_style(style_name: str) -> discord.ButtonStyle:
    styles = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger,
    }
    return styles.get(style_name, discord.ButtonStyle.secondary)


class RoleToggleButton(discord.ui.Button):
    def __init__(self, label: str, role_id: int, style: discord.ButtonStyle, emoji: str | None = None, row: int | None = None):
        super().__init__(label=label, style=style, emoji=emoji, row=row, custom_id=f"role_toggle:{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(self.role_id) if guild else None

        if not role:
            await interaction.response.send_message("❌ Role not found.", ephemeral=True)
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Self-assign role toggle")
                await interaction.response.send_message(f"✅ Removed role: {role.name}", ephemeral=True)
            else:
                await member.add_roles(role, reason="Self-assign role toggle")
                await interaction.response.send_message(f"✅ Added role: {role.name}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage that role.", ephemeral=True)


class RoleToggleView(discord.ui.View):
    def __init__(self, buttons_config: list[dict]):
        super().__init__(timeout=None)
        for btn in buttons_config:
            self.add_item(
                RoleToggleButton(
                    label=btn["label"],
                    role_id=int(btn["role_id"]),
                    style=button_style(btn.get("style", "secondary")),
                    emoji=btn.get("emoji"),
                    row=btn.get("row")
                )
            )


class Roles(commands.Cog, name="roles"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.template = load_roles_template()
        # Register persistent view
        if self.template.get("buttons"):
            self.bot.add_view(RoleToggleView(self.template["buttons"]))

    @app_commands.command(name="muroles", description="Post the role buttons.")
    @commands.has_permissions(manage_roles=True)
    async def roles(self, interaction: discord.Interaction) -> None:
        self.template = load_roles_template()
        buttons = self.template.get("buttons", [])

        if not buttons:
            await interaction.response.send_message("No buttons configured in templates/roles.json.", ephemeral=True)
            return

        embed = discord.Embed(
            title=self.template.get("title", "Choose your roles"),
            description=self.template.get("description", "Click a button to toggle roles."),
            color=int(self.bot.config.get("colors", {}).get("primary", "0x154273"), 16)
        )

        await interaction.response.send_message(embed=embed, view=RoleToggleView(buttons))


async def setup(bot) -> None:
    await bot.add_cog(Roles(bot))