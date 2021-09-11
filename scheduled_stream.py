import discord

from .errors import (
    APIError,
    OfflineStream,
    InvalidYoutubeCredentials,
    StreamNotFound,
    StreamsError,
    YoutubeQuotaExceeded,
)
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_number, humanize_timedelta

import logging
import asyncio
import aiohttp
from random import choice
from datetime import datetime, timezone
from string import ascii_letters
import xml.etree.ElementTree as ET
from typing import Optional, List, Tuple
from dateutil.parser import parse as parse_time

# YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
# YOUTUBE_CHANNELS_ENDPOINT = YOUTUBE_BASE_URL + "/channels"
# YOUTUBE_SEARCH_ENDPOINT = YOUTUBE_BASE_URL + "/search"
# YOUTUBE_VIDEOS_ENDPOINT = YOUTUBE_BASE_URL + "/videos"
# YOUTUBE_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# youtube_url_format_1 = "https://youtube.com/watch?v={}"

_ = Translator("StarStreams", __file__)
log = logging.getLogger("red.core.cogs.StarStreams")

class ScheduledStream():
    def __init__(self, **kwargs):
        self.text_channel_id = kwargs.pop("text_channel_id", None)
        # self.name = kwargs.pop("name", None)
        self.description = kwargs.pop("description", None)
        self.channel_ids = kwargs.pop("channel_ids", [])
        self.channel_names = kwargs.pop("channel_names", [])
        self.video_ids = kwargs.pop("video_ids", [])
        # if len(self.channel_names) > len(self.video_ids):
            # self.video_ids += [None] * (len(self.channel_names) - len(self.video_ids))
        self.time = kwargs.pop("time", None)
        self.message_id = kwargs.pop("message_id", None)
        self.streaming_sent = kwargs.pop("streaming_sent", False)
        self.change_channel_name = kwargs.pop("change_channel_name", True)
        self._token = kwargs.pop("token", None)
        self._config = kwargs.pop("config")
        self._bot = kwargs.pop("_bot")

    def export(self):
        data = {}
        for k, v in self.__dict__.items():
            if not k.startswith("_"):
                data[k] = v
        return data
    
    def get_time(self):
        return parse_time(self.time)

    def add_collab(self, channel_id, channel_name):
        if channel_id not in self.channel_ids:
            self.channel_ids.append(channel_id)
            self.channel_names.append(channel_name)
            self.video_ids.append("")

    # @classmethod
    # def make_embed(self, data):
    #     vid_data = data["items"][0]
    #     video_url = youtube_url_format_1.format(vid_data["id"])
    #     title = vid_data["snippet"]["title"]
    #     thumbnail = vid_data["snippet"]["thumbnails"]["medium"]["url"]
    #     # channel_title = vid_data["snippet"]["channelTitle"]
    #     embed = discord.Embed(title=title, url=video_url)
    #     embed.set_author(name=vid_data["snippet"]["channelTitle"])
    #     def rnd(url):
    #         """Appends a random parameter to the url to avoid Discord's caching"""
    #         return url + "?rnd=" + "".join([choice(ascii_letters) for _loop_counter in range(6)])
    #     embed.set_image(url=rnd(thumbnail))
    #     embed.colour = 0x9255A5
    #     return embed

    # @classmethod
    # def get_info(self, data):
    #     vid_data = data["items"][0]
    #     time = vid_data.get("liveStreamingDetails", {}).get("scheduledStartTime", None)
    #     return {
    #         "video_id": vid_data["id"],
    #         "channel_name": vid_data["snippet"]["channelTitle"],
    #         "title": vid_data["snippet"]["title"],
    #         "channel_id": vid_data["snippet"]["channelId"],
    #         "time": parse_time(time) if time else None
    #     }

