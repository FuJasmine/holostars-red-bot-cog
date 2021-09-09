from .star_stream import StarStream


def setup(bot):
    cog = StarStream(bot)
    bot.add_cog(cog)
