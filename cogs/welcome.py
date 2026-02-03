"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""

import asyncio
import json
import os
import discord
from discord import app_commands
from discord.ext import commands
import datetime

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


class WelcomeView(discord.ui.View):
    """     
    Persistent view containing the three verification buttons.

    Using timeout=None and custom_id makes these buttons persist
    across bot restarts - they'll still work after the bot reconnects.
    """

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Nederlander",
        style=discord.ButtonStyle.success,
        custom_id="welcome_citizen",
        emoji="🇳🇱"
    )
    async def citizen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle citizen verification request."""
        await create_verification_channel(interaction, "citizen")

    @discord.ui.button(
        label="Foreigner",
        style=discord.ButtonStyle.primary,
        custom_id="welcome_foreigner",
        emoji="🌍"
    )
    async def foreigner_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle foreigner verification request."""
        await create_verification_channel(interaction, "foreigner")

    @discord.ui.button(
        label="Embassy Request",
        style=discord.ButtonStyle.danger,
        custom_id="welcome_embassy",
        emoji="🚨"
    )
    async def embassy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle embassy request."""
        await create_verification_channel(interaction, "embassy")


async def create_verification_channel(interaction: discord.Interaction, request_type: str) -> None:
    """
    Create a private verification ticket channel for the user.

    Args:
        interaction: The button interaction from the user
        request_type: One of "citizen", "foreigner", or "embassy"

    The channel is only visible to:
    - The requesting user
    - The bot itself
    - The relevant moderator roles (Border Control or Embassy handlers)
    """
    global config
    config = load_config()

    guild = interaction.guild
    user = interaction.user

    # Generate unique ticket ID
    config["ticket_counter"] += 1
    ticket_id = config["ticket_counter"]
    save_config(config)

    # Configure channel properties based on request type
    if request_type == "citizen":
        channel_name = f"citizen-{ticket_id}-{user.name}"
        role_ids = [config["roles"]["border_control"]]
        embed_color = discord.Color.green()
        request_title = "Citizenship Verification Request"
    elif request_type == "foreigner":
        channel_name = f"foreigner-{ticket_id}-{user.name}"
        role_ids = [config["roles"]["border_control"]]
        embed_color = discord.Color.blue()
        request_title = "Foreigner Verification Request"
    else:  # embassy
        channel_name = f"embassy-{ticket_id}-{user.name}"
        # Embassy requests notify multiple high-level roles
        role_ids = [
            config["roles"]["minister_foreign_affairs"],
            config["roles"]["president"],
            config["roles"]["vice_president"]
        ]
        embed_color = discord.Color.red()
        request_title = "Emergency Embassy Request"

    # Sanitize channel name (Discord requires lowercase, no spaces, max 100 chars)
    channel_name = channel_name.lower().replace(" ", "-")[:100]

    # Get the category to create the channel in (if configured)
    category = None
    if config["verification_category_id"]:
        category = guild.get_channel(config["verification_category_id"])

    # Set up channel permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            manage_messages=True,
            embed_links=True
        )
    }

    # Grant access to the relevant moderator roles
    for role_id in role_ids:
        if role_id:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )

    # Check if bot has permission to create channels in the category
    if category:
        bot_permissions = category.permissions_for(guild.me)
        if not bot_permissions.manage_channels:
            await interaction.response.send_message(
                f"I don't have permission to create channels in the **{category.name}** category.\n\n"
                "**Fix:** Go to the category settings > Permissions > Add the bot role with 'Manage Channels' enabled.",
                ephemeral=True
            )
            return

    # Create the ticket channel
    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Verification request by {user.name} | Type: {request_type} | ID: {ticket_id} | User ID: {user.id}"
        )
    except discord.Forbidden as e:
        error_msg = (
            "I don't have permission to create channels.\n\n"
            "**Possible fixes:**\n"
            "• Ensure the bot has 'Manage Channels' permission server-wide\n"
        )
        if category:
            error_msg += f"• Add the bot to the **{category.name}** category with 'Manage Channels' permission\n"
        error_msg += f"\n**Error:** {e}"
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    # Build list of role mentions to ping
    role_mentions = []
    for role_id in role_ids:
        if role_id:
            role = guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)

    # Create the ticket embed with request details
    embed = discord.Embed(
        title=f"📋 {request_title}",
        description=f"**User:** {user.mention}\n**Request Type:** {request_type.title()}\n**Ticket ID:** #{ticket_id}",
        color=embed_color,
        timestamp=datetime.datetime.now(datetime.UTC)
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name="Instructions for Moderators",
        value="Use `/approve` to approve this request\nUse `/deny` to deny this request",
        inline=False
    )
    embed.set_footer(text=f"User ID: {user.id}")

    # Send the ticket message, pinging relevant moderators
    mention_text = " ".join(role_mentions) if role_mentions else ""
    await channel.send(content=mention_text, embed=embed)

    instructions_embed = discord.Embed(
        title=f"Verificatie Uitvoeren",
        description=f"Beste {user.mention},\n\nBedankt voor het aanvragen van de Nederlandse nationaliteit. Voor verificatie vragen we je om een screenshot van je WarEra profiel te sturen.\n\nZodra een moderator je aanvraag heeft beoordeeld, ontvang je een bericht in dit kanaal.",
        color=embed_color
    )
    await channel.send(embed=instructions_embed)

    # Confirm to the user (only they can see this response)
    await interaction.response.send_message(
        f"Your verification channel has been created: {channel.mention}\n"
        "Please wait for a moderator to review your request.",
        ephemeral=True
    )


class Welcome(commands.Cog, name="welcome"):
    """Cog for welcome messages and verification system."""
    
    def __init__(self, bot) -> None:
        self.bot = bot
        self.bot.logger.info("Welcome cog initialized")
        # Add the persistent view when the cog is loaded
        self.bot.add_view(WelcomeView(bot))
        self.config = load_config()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Send a welcome message when a new member joins the server.

        The message includes the configured welcome text and the
        three verification buttons (Citizen, Foreigner, Embassy).
        """

        # Skip if no welcome channel is configured
        if not self.config.get("welcome_channel_id"):
            return

        channel = member.guild.get_channel(self.config["welcome_channel_id"])
        if not channel:
            return
        
        embed = discord.Embed(
            title=f"🇳🇱 Welcome to Nederland!",
            description=f"Welcome {member.mention}! We're glad to have you here.",
        )
        await member.guild.get_channel(1467513798779736098).send(embed=embed)

        # Create the welcome embed
        embed = discord.Embed(
            title="🇳🇱 Welcome to Nederland!",
            description=self.config.get("welcome_message", "Welcome!"),
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.UTC)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")

        # Send welcome message with verification buttons
        await channel.send(content=member.mention, embed=embed, view=WelcomeView(self.bot))

    @app_commands.command(name="nickname", description="Set a user's nickname in the server")
    @app_commands.describe(user="The user to change the nickname for", nickname="The new nickname")
    @commands.has_permissions(manage_nicknames=True)
    async def nickname(self, interaction: discord.Interaction, user: discord.Member, nickname: str):
        """
        Change a user's nickname in the server.

        :param interaction: The interaction that triggered the command.
        :param user: The member whose nickname is to be changed.
        :param nickname: The new nickname to set.
        """
        try:
            await user.edit(nick=nickname, reason=f"Nickname changed by {interaction.user.name}")
            await interaction.response.send_message(
                f"Successfully changed {user.mention}'s nickname to **{nickname}**.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to change that user's nickname.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"Failed to change nickname: {e}",
                ephemeral=True
            )
        
        # Log to the government log channel
        log_posted = False
        if self.config.get("log_channel_id"):
            log_channel = interaction.guild.get_channel(self.config["log_channel_id"])
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="Nickname aangepast",
                        description=f"**User:** {user.mention} ({user.name})\n",
                        color=discord.Color.green(),
                        timestamp=datetime.datetime.now(datetime.UTC)
                    )
                    log_embed.set_thumbnail(url=user.display_avatar.url)
                    log_embed.set_footer(
                        text=f"Veranderd door {interaction.user.name}",
                        icon_url=interaction.user.display_avatar.url
                    )
                    await log_channel.send(embed=log_embed)
                    log_posted = True
                except (discord.Forbidden, discord.HTTPException) as e:
                    self.bot.logger.error(f"Failed to post to log channel: {e}")

    @app_commands.command(name="approve", description="Approve a verification request")
    @app_commands.describe(reason="Internal reason for approval (not shown to user)")
    async def approve(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        """
        Approve a verification request in the current ticket channel.
        """
        channel = interaction.channel

        # Verify this is a ticket channel
        if not channel.name.startswith(("citizen-", "foreigner-", "embassy-")):
            await interaction.response.send_message(
                "This command can only be used in verification channels.",
                ephemeral=True
            )
            return

        # Check if the user has permission to moderate
        mod_roles = [
            self.config["roles"]["border_control"],
            self.config["roles"]["minister_foreign_affairs"],
            self.config["roles"]["president"],
            self.config["roles"]["vice_president"]
        ]

        user_role_ids = [role.id for role in interaction.user.roles]
        has_permission = any(role_id in user_role_ids for role_id in mod_roles if role_id)

        if not has_permission and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        # Extract user ID from channel topic
        topic = channel.topic or ""
        user_id = None
        for part in topic.split("|"):
            if "User ID:" in part:
                try:
                    user_id = int(part.split(":")[-1].strip())
                except ValueError:
                    pass

        if not user_id:
            await interaction.response.send_message(
                "Could not find the user for this request. Please check manually.",
                ephemeral=True
            )
            return

        member = interaction.guild.get_member(user_id)
        if not member:
            await interaction.response.send_message(
                "The user is no longer in the server.",
                ephemeral=True
            )
            return

        # Determine which role to grant based on request type
        request_type = channel.name.split("-")[0]
        role_to_give = None

        if request_type == "citizen":
            role_to_give = interaction.guild.get_role(self.config["roles"]["nederlander"])
        elif request_type == "foreigner":
            role_to_give = interaction.guild.get_role(self.config["roles"]["foreigner"])

        # Attempt to assign the role
        if role_to_give:
            try:
                await member.add_roles(role_to_give)
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"I don't have permission to assign the {role_to_give.name} role. "
                    "Make sure my bot role is **higher** than this role in Server Settings > Roles.",
                    ephemeral=True
                )
                return
            except discord.HTTPException as e:
                await interaction.response.send_message(
                    f"Failed to assign role: {e}",
                    ephemeral=True
                )
                return

        # Notify the user of approval
        user_embed = discord.Embed(
            title="✅ Request Approved!",
            description=f"Your {request_type} verification request has been approved!",
            color=discord.Color.green()
        )
        if role_to_give:
            user_embed.add_field(name="Role Granted", value=role_to_give.mention, inline=False)
        user_embed.set_footer(text="This channel will be deleted in 30 seconds.")

        await channel.send(content=member.mention, embed=user_embed)

        # Log to the government log channel
        log_posted = False
        if self.config.get("log_channel_id"):
            log_channel = interaction.guild.get_channel(self.config["log_channel_id"])
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="✅ Verification Approved",
                        description=f"**User:** {member.mention} ({member.name})\n"
                                f"**Type:** {request_type.title()}\n"
                                f"**Reason:** {reason}",
                        color=discord.Color.green(),
                        timestamp=datetime.datetime.now(datetime.UTC)
                    )
                    log_embed.set_thumbnail(url=member.display_avatar.url)
                    log_embed.set_footer(
                        text=f"Approved by {interaction.user.name}",
                        icon_url=interaction.user.display_avatar.url
                    )
                    if role_to_give:
                        log_embed.add_field(name="Role Granted", value=role_to_give.mention, inline=True)
                    await log_channel.send(embed=log_embed)
                    log_posted = True
                except (discord.Forbidden, discord.HTTPException) as e:
                    self.bot.logger.error(f"Failed to post to log channel: {e}")

        # Confirm to the moderator
        mod_embed = discord.Embed(
            title="📝 Approval Logged",
            description=f"**User:** {member.mention}\n**Type:** {request_type}\n**Reason:** {reason}",
            color=discord.Color.green()
        )
        mod_embed.set_footer(text=f"Approved by {interaction.user.name}")

        if not log_posted and self.config.get("log_channel_id"):
            mod_embed.add_field(name="⚠️ Warning", value="Could not post to log channel", inline=False)

        await interaction.response.send_message(embed=mod_embed, ephemeral=True)

        # Delete the ticket channel after a delay
        await asyncio.sleep(30)
        try:
            await channel.delete(reason=f"Verification approved by {interaction.user.name}")
        except (discord.NotFound, discord.Forbidden) as e:
            self.bot.logger.error(f"Could not delete channel: {e}")

    @app_commands.command(name="deny", description="Deny a verification request")
    @app_commands.describe(reason="Internal reason for denial (not shown to user)")
    async def deny(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        """
        Deny a verification request in the current ticket channel.
        """

        channel = interaction.channel

        # Verify this is a ticket channel
        if not channel.name.startswith(("citizen-", "foreigner-", "embassy-")):
            await interaction.response.send_message(
                "This command can only be used in verification channels.",
                ephemeral=True
            )
            return

        # Check if the user has permission to moderate
        mod_roles = [
            self.config["roles"]["border_control"],
            self.config["roles"]["minister_foreign_affairs"],
            self.config["roles"]["president"],
            self.config["roles"]["vice_president"]
        ]

        user_role_ids = [role.id for role in interaction.user.roles]
        has_permission = any(role_id in user_role_ids for role_id in mod_roles if role_id)

        if not has_permission and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        # Extract user ID from channel topic
        topic = channel.topic or ""
        user_id = None
        for part in topic.split("|"):
            if "User ID:" in part:
                try:
                    user_id = int(part.split(":")[-1].strip())
                except ValueError:
                    pass

        member = interaction.guild.get_member(user_id) if user_id else None
        request_type = channel.name.split("-")[0]

        # Notify the user of denial
        user_embed = discord.Embed(
            title="❌ Request Denied",
            description=f"Your {request_type} verification request has been denied.",
            color=discord.Color.red()
        )
        user_embed.set_footer(text="This channel will be deleted in 30 seconds.")

        if member:
            await channel.send(content=member.mention, embed=user_embed)
        else:
            await channel.send(embed=user_embed)

        # Log to the government log channel
        log_posted = False
        if self.config.get("log_channel_id"):
            log_channel = interaction.guild.get_channel(self.config["log_channel_id"])
            if log_channel:
                try:
                    log_embed = discord.Embed(
                        title="❌ Verification Denied",
                        description=f"**User:** {member.mention if member else 'Unknown'} "
                                f"({member.name if member else 'Unknown'})\n"
                                f"**Type:** {request_type.title()}\n"
                                f"**Reason:** {reason}",
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now(datetime.UTC)
                    )
                    if member:
                        log_embed.set_thumbnail(url=member.display_avatar.url)
                    log_embed.set_footer(
                        text=f"Denied by {interaction.user.name}",
                        icon_url=interaction.user.display_avatar.url
                    )
                    await log_channel.send(embed=log_embed)
                    log_posted = True
                except (discord.Forbidden, discord.HTTPException) as e:
                    self.bot.logger.error(f"Failed to post to log channel: {e}")

        # Confirm to the moderator
        mod_embed = discord.Embed(
            title="📝 Denial Logged",
            description=f"**User:** {member.mention if member else 'Unknown'}\n"
                    f"**Type:** {request_type}\n"
                    f"**Reason:** {reason}",
            color=discord.Color.red()
        )
        mod_embed.set_footer(text=f"Denied by {interaction.user.name}")

        if not log_posted and self.config.get("log_channel_id"):
            mod_embed.add_field(name="⚠️ Warning", value="Could not post to log channel", inline=False)

        await interaction.response.send_message(embed=mod_embed, ephemeral=True)

        # Delete the ticket channel after a delay
        await asyncio.sleep(30)
        try:
            await channel.delete(reason=f"Verification denied by {interaction.user.name}")
        except (discord.NotFound, discord.Forbidden) as e:
            self.bot.logger.error(f"Could not delete channel: {e}")

    @commands.command(name="testwelcome")
    @commands.is_owner()
    async def testwelcome(self, context: commands.Context):
        """Simulate a member join for testing"""
        await self.on_member_join(context.author)


async def setup(bot) -> None:
    await bot.add_cog(Welcome(bot))