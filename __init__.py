from .star_stream import StarStream
import json
from pathlib import Path
from .temprole import TempRole

with open(Path(__file__).parent / "info.json") as fp:
    __red_end_user_data_statement__ = json.load(fp)["end_user_data_statement"]

def setup(bot):
    temp_role = TempRole(bot)
    bot.add_cog(temp_role)
    star_stream_cog = StarStream(bot, temp_role)
    bot.add_cog(star_stream_cog)
