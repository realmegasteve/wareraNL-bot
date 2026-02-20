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


TEMPLATES_PATH = f"{os.path.realpath(os.path.dirname(__file__))}/../../templates"


def load_roles_template(path: str=f"{TEMPLATES_PATH}/mu_roles.json") -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
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
    def __init__(
            self, label: str, role_id: int, style: discord.ButtonStyle, 
            emoji: str | None = None, row: int | None = None, 
            secondary_role_id: int | None = None):
        super().__init__(
            label=label, style=style, emoji=emoji, row=row, 
            custom_id=f"role_toggle:{role_id}")
        self.role_id = role_id
        self.secondary_role_id = secondary_role_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        if not guild:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        role = guild.get_role(self.role_id)
        secondary_role = guild.get_role(self.secondary_role_id) if self.secondary_role_id else None

        if not role:
            await interaction.response.send_message("❌ Role not found.", ephemeral=True)
            return

        try:
            # Collect primary roles defined on this view
            primary_roles: list[discord.Role] = []
            for child in getattr(self.view, "children", []):
                if isinstance(child, RoleToggleButton):
                    r = guild.get_role(child.role_id)
                    if r:
                        primary_roles.append(r)

            # Which primary roles the member currently has
            member_primary_roles = [r for r in primary_roles if r in member.roles]

            # If user clicked a primary they already have -> remove that primary only
            if role in member.roles:
                await member.remove_roles(role, reason="Self-assign role toggle")
                await interaction.response.send_message(f"✅ Removed role: {role.name}", ephemeral=True)
                return

            # We're adding a primary role
            # If exclusive, remove any other primary roles the member has
            if getattr(self.view, "exclusive", False):
                roles_to_remove = [r for r in member_primary_roles if r != role]
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="Self-assign role exclusive toggle")

            # Build list of roles to add: always add the selected primary; add secondary only if user doesn't have it
            roles_to_add = [role]
            if secondary_role and secondary_role not in member.roles:
                roles_to_add.append(secondary_role)

            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Self-assign role toggle")
                names = ", ".join(r.name for r in roles_to_add)
                await interaction.response.send_message(f"✅ Added role(s): {names}", ephemeral=True)
            else:
                await interaction.response.send_message("✅ No roles to add.", ephemeral=True)

        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage that role.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("❌ An error occurred while toggling the role.", ephemeral=True)

class RoleToggleView(discord.ui.View):
    def __init__(self, buttons_config: list[dict], exclusive: bool = False):
        super().__init__(timeout=None)
        self.exclusive = exclusive
        for btn in buttons_config:
            self.add_item(
                RoleToggleButton(
                    label=btn["label"],
                    role_id=int(btn["role_id"]),
                    style=button_style(btn.get("style", "secondary")),
                    emoji=btn.get("emoji"),
                    row=btn.get("row"),
                    secondary_role_id=int(btn["secondary_role_id"]) if btn.get("secondary_role_id") else None,
                )
            )


class Roles(commands.Cog, name="roles"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.template = load_roles_template()
        # Register persistent views for templates so buttons keep working after restarts
        if self.template.get("buttons"):
            self.bot.add_view(RoleToggleView(self.template["buttons"], exclusive=True))

        # Also load and register the general roles template (templates/roles.json)
        try:
            general_template = load_roles_template(f"{TEMPLATES_PATH}/roles.json")
            if general_template.get("buttons"):
                self.bot.add_view(RoleToggleView(general_template["buttons"]))
        except Exception:
            # Fail quietly; commands will still load and can post the view manually
            pass

    @app_commands.command(name="muroles", description="Post the MU role buttons.")
    @commands.has_permissions(manage_roles=True)
    async def muroles(self, interaction: discord.Interaction) -> None:
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

        await interaction.response.send_message("✅ Posted role buttons to the channel.", ephemeral=True)
        await interaction.channel.send(embed=embed, view=RoleToggleView(buttons, exclusive=True))

    @app_commands.command(name="generalroles", description="Post the general role buttons.")
    @commands.has_permissions(manage_roles=True)
    async def generalroles(self, interaction: discord.Interaction) -> None:
        self.template = load_roles_template(f"{TEMPLATES_PATH}/roles.json")
        buttons = self.template.get("buttons", [])

        if not buttons:
            await interaction.response.send_message("No buttons configured in templates/roles.json.", ephemeral=True)
            return

        embed = discord.Embed(
            title=self.template.get("title", "Kies je rollen"),
            description=self.template.get("description", "Klik op een knop om rollen te toggelen."),
            color=int(self.bot.config.get("colors", {}).get("primary", "0x154273"), 16)
        )

        # Send ephemeral confirmation to user
        await interaction.response.send_message("✅ Posted role buttons to the channel.", ephemeral=True)
        # Send embed to channel
        await interaction.channel.send(embed=embed, view=RoleToggleView(buttons))

    @app_commands.command(name="muwachtlijst", description="Tel het aantal mensen op de wachtlijst voor MU's.")
    async def muwachtlijst(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        wachtlijst_role = guild.get_role(self.bot.config["roles"]["wachtlijst"])  # Wachtlijst role ID
        if not wachtlijst_role:
            await interaction.response.send_message("❌ Wachtlijst role not found.", ephemeral=True)
            return

        count = len(wachtlijst_role.members)
        await interaction.response.send_message(f"📋 Er zijn momenteel {count} mensen op de wachtlijst voor MU's.")

    @app_commands.command(name="ambassadeurs", description="Geef de ambassadeur rol.")
    @app_commands.describe(user="De gebruiker aan wie je de ambassadeur rol wilt geven.")
    async def ambassadeurs(self, interaction: discord.Interaction, user: discord.Member) -> None:
        # check if command is used by minister van buitenlandse zaken
        if not any(role.id == self.bot.config["roles"]["minister_foreign_affairs"] for role in interaction.user.roles):
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        ambassadeur_role = guild.get_role(self.bot.config["roles"]["ambassadeur"])  # Ambassadeur role ID
        if not ambassadeur_role:
            await interaction.response.send_message("❌ Ambassadeur role not found.", ephemeral=True)
            return

        try:
            await user.add_roles(ambassadeur_role, reason="Toegewezen door Minister van Buitenlandse Zaken")
            await interaction.response.send_message(f"✅ {user.mention} is nu een Ambassadeur!")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage that role.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("❌ An error occurred while assigning the role.", ephemeral=True)

async def setup(bot) -> None:
    await bot.add_cog(Roles(bot))