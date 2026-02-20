"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""

import json
import os
import discord
from discord.ext import commands
from discord.ext.commands import Context

from cogs.standard_messages.generate import GenerateEmbeds


class MUs(GenerateEmbeds, name="mus"):
    def __init__(self, bot) -> None:
        super().__init__(bot)
        self.load_json(f"{os.path.realpath(os.path.dirname(__file__))}/../../templates/mus.json")
    
    @commands.hybrid_command(
        name="mulijst",
        description="Post de MU lijst in het huidige kanaal.",
    )
    @commands.has_permissions(manage_messages=True)
    async def mulijst(self, context: Context) -> None:
        """
        Post de MU lijst als een reeks embeds.

        :param context: The hybrid command context.
        """
        
        if not self.json_data or not self.json_data.get("embeds"):
            embed = discord.Embed(
                description="MU data niet gevonden. Gebruik `/reloadmus` om opnieuw te laden.",
                color=self.get_color("error")
            )
            await context.send(embed=embed, ephemeral=True)
            return
        
        # Send confirmation
        await context.send("📚 Bezig met posten van de MU lijst...", ephemeral=True)
        
        # send MU explanation
        embed = discord.Embed(
            title="MU Soorten",
            description=f"- **Elite MU**: Deze MU's zullen als eerste ingezet worden tijdens gevechten. Dit betekent dat ze geacht worden een voorraad aan equipment, munitie, eten, pillen en geld beschikbaar te houden. Daarnaast wordt actieve deelname aan oorlogen verwacht.\n"
                        f"- **Eco MU**: Leden van deze MU's zullen tijdens oorlogen in eco stand blijven om de staatskas aan te vullen. Hiervan wordt verwacht dat leden actief doneren aan de staatskas tijdens oorlogen om bounties te kunnen betalen.\n"
                        f"- **Overige MU**: Van overige MU's wordt niet veel gevraagd, behalve dat ze meevechten tijdens oorlogen. In de aanloop naar oorlogen kunnen leden aanwijzingen volgen van de regering, maar er wordt niet verwacht altijd een voorraad beschikbaar te hebben.",
            color=discord.Color.gold()
        )
        await context.channel.send(embed=embed)

        # Send all embeds
        for embed_data in self.json_data["embeds"]:
            try:
                embed = self.create_embed_from_data(embed_data)
                await context.channel.send(embed=embed)
            except Exception as e:
                self.bot.logger.error(f"Error sending embed: {e}")
        
        self.bot.logger.info(f"MU lijst posted by {context.author} in {context.channel.name}")

    @commands.hybrid_command(
        name="reloadmus",
        description="Herlaad de MU JSON file.",
    )
    @commands.is_owner()
    async def reloadmus(self, context: Context) -> None:
        """
        Reload the MU from the JSON file.
        :param context: The hybrid command context.
        """
        try:
            self.load_json(f"{os.path.realpath(os.path.dirname(__file__))}/../../templates/mus.json")
            print(self.json_data)
            embed = discord.Embed(
                description=f"✅ MU succesvol herladen! ({len(self.json_data.get('embeds', []))} embeds)",
                color=self.get_color("success")
            )
            await context.send(embed=embed)
            self.bot.logger.info(f"MU reloaded by {context.author}")
        except Exception as e:
            embed = discord.Embed(
                description=f"❌ Fout bij herladen: {e}",
                color=self.get_color("error")
            )
            await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(MUs(bot))
