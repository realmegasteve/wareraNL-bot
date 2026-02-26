"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""


from discord.ext import commands
from discord.ext.commands import Context
from discord import ui
import discord

selectedAdvertentie: int
kanaalenLijst = {
0:"",
1:"",
2:"",
3:""
}

class advertentieSelectClass(discord.ui.Select, custom_id="advertentieSelect", options=["MU", "Market", "Company", "Party"]):


    async def interaction_check(self ,interaction: discord.Interaction):
        await interaction.response.send_modal(advertentieModalClass())


class advertentieModalClass(ui.modal, title="Maak een advertentie", custom_id="advertentieModal"):
    advertentieType = ui.TextInput(
        label="advertentie type: (MU, Market, Company, Party)"
    )
    advertentieTitle = ui.TextInput(
        label="Advertentie Title"
    )
    advertentieDescription = ui.TextInput(
        label="description",
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):

        await messagesubmit(self,)



async def messagesubmit(self, interaction: discord.Interaction):
    global selectedAdvertentie
    selectedAdvertentie = advertentieSelectClass().id

    if selectedAdvertentie == 0:
        modal = advertentieModalClass

        modal.add_item(discord.ui.TextInput(
            label="Naam van de MU"
        ))
        modal.add_item(discord.ui.TextInput(
            label="Nodig om de MU te joinen"
        ))

    elif selectedAdvertentie == 1:
        modal = advertentieModalClass

        modal.add_item(discord.ui.TextInput(
            label="Product naam"
        ))
        modal.add_item(discord.ui.TextInput(
            label="Product prijs"
        ))

    elif selectedAdvertentie == 2:
        modal = advertentieModalClass

        modal.add_item(discord.ui.TextInput(
            label="Bedrijf naam"
        ))
        modal.add_item(discord.ui.TextInput(
            label="Product dat gemaakt wordt"
        ))
        modal.add_item(discord.ui.TextInput(
            label="Betaaling"
        ))
        modal.add_item(discord.ui.TextInput(
            label="Hoeveel"
        ))

    elif selectedAdvertentie == 3:
        modal = advertentieModalClass

        modal.add_item(discord.ui.TextInput(
            label="Naam van de MU"
        ))
        modal.add_item(discord.ui.TextInput(
            label="Nodig om de MU te joinen"
        ))
    await interaction.response.send_modal(advertentieModalClass())
    channel = discord.Client.get_channel()
    await channel.send("test Response : this is sent so the developers know this command is active if this is sent then contact the developers to remove it")




# Here we name the cog and create a new class for the cog.
class Advertentie(commands.Cog, name="advertentie"):
    def __init__(self, bot) -> None:
        self.bot = bot

    # Here you can just add your own commands, you'll always need to provide "self" as first parameter.

    @commands.hybrid_command(
        name="Advertentie",
        description="This command allows you to make an advertisement",
    )
    async def advertentie(self, interaction: discord.Interaction) -> None:


        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        # Do your stuff here

        # Don't forget to remove "pass", I added this just because there's no content in the method.
        pass


# And then we finally add the cog to the bot so that it can load, unload, reload and use it's content.
async def setup(bot) -> None:
    await bot.add_cog(Advertentie(bot))
