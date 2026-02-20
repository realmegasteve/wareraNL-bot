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
from discord.ext import commands, tasks
import datetime
import logging

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


# Add a global dictionary to track open tickets
open_tickets = {}

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
    global config, open_tickets

    user = interaction.user

    # Check if the user already has an open ticket
    if user.id in open_tickets:
        existing_channel = open_tickets[user.id]
        await interaction.response.send_message(
            f"You already have an open ticket: {existing_channel.mention}. Please resolve it before creating a new one.",
            ephemeral=True
        )
        return

    config = load_config()
    guild = interaction.guild

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

    # Track the open ticket
    open_tickets[user.id] = channel

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

    if request_type == "citizen":
        instructions_embed = discord.Embed(
            title=f"Verificatie Uitvoeren",
            description=f"Beste {user.mention},\n\nBedankt voor het aanvragen van de Nederlandse nationaliteit. Voor verificatie vragen we je om een screenshot van je WarEra profiel te sturen.\n\nZodra een moderator je aanvraag heeft beoordeeld, ontvang je een bericht in dit kanaal.",
            color=embed_color
        )
    elif request_type == "foreigner":
        instructions_embed = discord.Embed(
            title=f"Perform Verification",
            description=f"Dear {user.mention},\n\nThank you for applying for foreigner status. For verification, please provide a screenshot of your WarEra profile.\n\nOnce a moderator has reviewed your application, you will receive a message in this channel.",
            color=embed_color
        )
    else:  # embassy
        instructions_embed = discord.Embed(
            title=f"Embassy Request Instructions",
            description=f"Dear {user.mention},\n\nThank you for applying for an embassy request. For verification, please provide a screenshot of your WarEra profile. \n\nA moderator will review your request and respond in this channel as soon as possible.",
            color=embed_color
        )
    await channel.send(content=user.mention, embed=instructions_embed)

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

    def cog_load(self) -> None:
        """Start the scheduled tasks when the cog is loaded."""
        self.daily_bezoeker_ping.start()

    def cog_unload(self) -> None:
        """Cancel scheduled tasks when the cog is unloaded."""
        self.daily_bezoeker_ping.cancel()

    @tasks.loop(time=datetime.time(19, 0))  # Runs daily at 19:00
    async def daily_bezoeker_ping(self):
        """Send a daily ping to the bezoeker role in the welcome channel."""
        try:
            # Get the welcome channel
            welcome_channel_id = self.config.get("welcome_channel_id")
            if not welcome_channel_id:
                self.bot.logger.warning("Welcome channel ID not configured")
                return

            # Find the welcome channel across all guilds the bot is in
            for guild in self.bot.guilds:
                channel = guild.get_channel(welcome_channel_id)
                if channel:
                    # Get the bezoeker role
                    bezoeker_role_id = self.config.get("roles", {}).get("bezoeker")
                    if not bezoeker_role_id:
                        self.bot.logger.warning("Bezoeker role ID not configured")
                        return

                    role = guild.get_role(bezoeker_role_id)
                    if not role:
                        self.bot.logger.warning(f"Bezoeker role not found in guild {guild.name}")
                        return

                    # Send the ping
                    await channel.send(f"{role.mention} please use one of the above buttons to claim your role.")
                    self.bot.logger.info(f"Sent daily bezoeker ping in {guild.name}")
                    return

            self.bot.logger.warning(f"Welcome channel {welcome_channel_id} not found in any guild")
        except Exception as e:
            self.bot.logger.error(f"Error sending daily bezoeker ping: {e}")

    @daily_bezoeker_ping.before_loop
    async def before_daily_ping(self):
        """Ensure the bot is ready before starting the scheduled task."""
        await self.bot.wait_until_ready()

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
        
        default_role_id = self.config["roles"]["bezoeker"]
        if default_role_id:
            role = member.guild.get_role(default_role_id)
            if role:
                await member.add_roles(role)

        embed = discord.Embed(
            title=f"🇳🇱 Welcome to Nederland!",
            description=f"Welcome {member.mention}! We're glad to have you here.",
            color=int(self.bot.config.get("colors", {}).get("primary", "0x154273"), 16)
        )
        await member.guild.get_channel(1401530718223335499).send(embed=embed)

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

        # Remove old role
        old_role_id = self.config["roles"]["bezoeker"]
        old_role = interaction.guild.get_role(old_role_id)
        if old_role:
            try:
                await member.remove_roles(old_role)
            except discord.Forbidden:
                self.bot.logger.error(
                    f"Could not remove role {old_role.name} from {member.name} due to permission issues."
                )
            except discord.HTTPException as e:
                self.bot.logger.error(
                    f"Failed to remove role {old_role.name} from {member.name}: {e}"
                )


        # Notify the user of approval
        if not request_type == "citizen":
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

        if request_type == "citizen":
            welcome_embed = discord.Embed(
                title="Welkom Nederlander! 🇳🇱",
                description=f"Welkom {member.mention} in WarEra Nederland!\n\n"
                            f"Om je op weg te helpen, bekijk onze <#1457351757444419769> en claim je rollen in <#1456612515902390353>. "
                            f"Voor vragen kun terecht in <#1456252976967581877>.\n\n"
                            f"Als laatste: je kan op je profiel bij `Settings > Referrals` een referrer opgeven, vul hier het liefst een **Nederlander** in (bijvoorbeeld *{interaction.user.nick}*), dan krijgen jij en de referrer muntjes.",
                color=discord.Color.gold(),
            )
            welcome_embed.set_thumbnail(url=member.display_avatar.url)
            welcome_embed.set_footer(text="Dit kanaal zal worden verwijderd over 1 uur.")
            self.bot.logger.info(f"Sending welcome message to {member.name} in {interaction.guild.name}")
            await channel.send(
                content=member.mention, embed=welcome_embed)

        # Delete the ticket channel after a delay
        if not request_type == "citizen":
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(3600)  # Give new citizens more time to read the welcome message
        try:
            await channel.delete(reason=f"Verification approved by {interaction.user.name}")
        except (discord.NotFound, discord.Forbidden) as e:
            self.bot.logger.error(f"Could not delete channel: {e}")

        # remove ticket from tracking
        if member.id in open_tickets:
            del open_tickets[member.id]

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

        
        # remove ticket from tracking
        if member.id in open_tickets:
            del open_tickets[member.id]

    @app_commands.command(name="embassyapprove", description="Approve an embassy request")
    @app_commands.describe(country="Country of the embassy request")
    async def embassy_approve(self, interaction: discord.Interaction, country: str):
        """
        Approve an embassy request and assign the corresponding role.

        This command is similar to /approve but also assigns the specific embassy role.
        """
        import traceback
        try:
            # avoid "The application did not respond" (Discord requires a response within 3s)
            await interaction.response.defer(ephemeral=True)
            # quick trace so you can see the command started
            self.bot.logger.info(f"embassy_approve started by {interaction.user} for country={country}")

            # helper to reply whether we've already deferred
            async def reply(content=None, **kwargs):
                if interaction.response.is_done():
                    await interaction.followup.send(content, **kwargs)
                else:
                    await interaction.response.send_message(content, **kwargs)
            channel = interaction.channel
            guild = interaction.guild

            
            minister_role = interaction.guild.get_role(self.config["roles"]["government"])
            foreign_minister_role = interaction.guild.get_role(self.config["roles"]["minister_foreign_affairs"])

            # Check if the user has permission to moderate
            mod_roles = [
                self.config["roles"]["government"],
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

            self.bot.logger.debug(f"looking for user ID in channel topic: {channel.topic}")
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

            # Attempt to assign the embassy role based on country
            self.bot.logger.debug(f"Assigning embassy role for country: {country}")
            embassy_role = interaction.guild.get_role(1456283384816074813)

            try:
                await member.add_roles(embassy_role)
            except discord.Forbidden:
                await interaction.response.send_message(
                    f"I don't have permission to assign the {embassy_role.name} role. "
                    "Make sure my bot role is **higher** than this role in Server Settings > Roles.",
                    ephemeral=True
                )
                return

            # remove visitor role
            old_role_id = self.config["roles"]["bezoeker"]
            old_role = interaction.guild.get_role(old_role_id)
            if old_role:
                try:
                    await member.remove_roles(old_role)
                except discord.Forbidden:
                    self.bot.logger.error(
                        f"Could not remove role {old_role.name} from {member.name} due to permission issues."
                    )
                except discord.HTTPException as e:
                    self.bot.logger.error(
                        f"Failed to remove role {old_role.name} from {member.name}: {e}"
                    )

            # Check if the embassy channel exists
            self.bot.logger.debug(f"Checking for existing embassy channel for country: {country}")
            embassy_channel = None
            for channel in interaction.guild.channels:
                if channel.name == f"{country.lower()}-embassy" or channel.name == f"{country.lower()}-ambassade":
                    embassy_channel = channel
                    break

            if not embassy_channel:
                # Create the ticket channel
                self.bot.logger.debug(f"Creating embassy channel for country: {country}")
                channel_name = f"{country.lower()}-embassy"
                category = interaction.guild.get_channel(1456259898349453433)

                # Set up channel permissions
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    minister_role: discord.PermissionOverwrite(
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

                try:
                    channel = await guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        overwrites=overwrites,
                        topic=f"Embassy channel for {country}"
                    )
                    embassy_channel = channel
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

            if embassy_channel:
                self.bot.logger.debug(f"Setting permissions for member {member} in embassy channel {embassy_channel.name}")
                # try:
                await embassy_channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
                # except discord.Forbidden:
                #     self.bot.logger.error(
                #         f"Could not set permissions for {member.name} in {embassy_channel.name} due to permission issues."
                #     )
                # except discord.HTTPException as e:
                #     self.bot.logger.error(
                #         f"Failed to set permissions for {member.name} in {embassy_channel.name}: {e}"
                #     )
            self.bot.logger.debug(f"Successfully approved embassy request for {member.name} and assigned role {embassy_role.name}")
            confirmation_embed = discord.Embed(
                title=f"Welcome to {country.title()} Embassy! 🇳🇱",
            )
            # send confirmation in embassy channel
            await embassy_channel.send(content=f"{member.mention} {minister_role.mention}", embed=confirmation_embed)
            
            await reply(
                f"Successfully approved embassy request for {member.mention} and assigned role {embassy_role.mention}. "
                f"Access to the embassy channel {embassy_channel.mention} has been granted."
            )

            # Log to the government log channel
            log_posted = False
            if self.config.get("log_channel_id"):
                log_channel = interaction.guild.get_channel(self.config["log_channel_id"])
                if log_channel:
                    try:
                        log_embed = discord.Embed(
                            title="✅ Embassy Request Approved",
                            description=f"**User:** {member.mention} ({member.name})\n"
                                        f"**Country:** {country.title()}\n",
                            color=discord.Color.green(),
                            timestamp=datetime.datetime.now(datetime.UTC)
                        )
                        log_embed.set_thumbnail(url=member.display_avatar.url)
                        log_embed.set_footer(
                            text=f"Approved by {interaction.user.name}",
                            icon_url=interaction.user.display_avatar.url
                        )
                        await log_channel.send(embed=log_embed)
                        log_posted = True
                    except (discord.Forbidden, discord.HTTPException) as e:
                        self.bot.logger.error(f"Failed to post to log channel: {e}")

            # Delete the ticket channel after a delay
            await asyncio.sleep(30)
            try:
                await interaction.channel.delete(reason=f"Embassy request approved by {interaction.user.name}")
            except (discord.NotFound, discord.Forbidden) as e:
                self.bot.logger.error(f"Could not delete channel: {e}")

            
            # remove ticket from tracking
            if member.id in open_tickets:
                del open_tickets[member.id]



                
        except Exception as e:
            traceback.print_exception(type(e), e, e.__traceback__)
            self.bot.logger.error("Unhandled error in embassy_approve", exc_info=True)
            try:
                if interaction and hasattr(interaction, "response") and interaction.response.is_done():
                    await interaction.followup.send("An internal error occurred while running this command.", ephemeral=True)
                else:
                    await interaction.response.send_message("An internal error occurred while running this command.", ephemeral=True)
            except Exception:
                traceback.print_exc()
            return


    

    @commands.command(name="testwelcome")
    @commands.is_owner()
    async def testwelcome(self, context: commands.Context):
        """Simulate a member join for testing"""
        await self.on_member_join(context.author)


async def setup(bot) -> None:
    await bot.add_cog(Welcome(bot))