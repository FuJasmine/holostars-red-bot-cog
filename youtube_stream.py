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

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
YOUTUBE_CHANNELS_ENDPOINT = YOUTUBE_BASE_URL + "/channels"
YOUTUBE_SEARCH_ENDPOINT = YOUTUBE_BASE_URL + "/search"
YOUTUBE_VIDEOS_ENDPOINT = YOUTUBE_BASE_URL + "/videos"
YOUTUBE_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

youtube_url_format_1 = "https://youtube.com/watch?v={}"

_ = Translator("StarStreams", __file__)
log = logging.getLogger("red.core.cogs.StarStreams")

class YouTubeStream():
    token_name = "youtube"

    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        self._token = kwargs.pop("token", None)
        self._config = kwargs.pop("config")
        self.not_livestreams: List[str] = []
        self.livestreams: List[str] = kwargs.pop("livestreams", [])
        self.mention: List[int] = kwargs.pop("mention", [])

        self._bot = kwargs.pop("_bot")
        self.name = kwargs.pop("name", None)
        # self.already_online = kwargs.pop("already_online", False)
        self.messages = kwargs.pop("messages", [])
        self.scheduled_sent = kwargs.pop("scheduled_sent", [])
        self.streaming_sent = kwargs.pop("streaming_sent", [])
        self.chat_channel_id = kwargs.pop("chat_channel_id", None)
        self.mention_channel_id = kwargs.pop("mention_channel_id", None)
        self.emoji = kwargs.pop("emoji", None)
        self.type = self.__class__.__name__

    async def check_exists(self):
        try:
            await self.is_online()
        except OfflineStream:
            pass
        except StreamNotFound:
            return False
        except StreamsError:
            raise
        return True
    
    def export(self):
        data = {}
        for k, v in self.__dict__.items():
            if not k.startswith("_"):
                data[k] = v
        return data

    async def is_online(self):
        if not self._token:
            raise InvalidYoutubeCredentials("YouTube API key is not set.")

        if not self.id:
            self.id = await self.fetch_id()
        elif not self.name:
            self.name = await self.fetch_name()

        async with aiohttp.ClientSession() as session:
            async with session.get(YOUTUBE_CHANNEL_RSS.format(channel_id=self.id)) as r:
                if r.status == 404:
                    log.warning(YOUTUBE_CHANNEL_RSS.format(channel_id=self.id))
                    raise StreamNotFound()
                rssdata = await r.text()

        if self.not_livestreams:
            self.not_livestreams = list(dict.fromkeys(self.not_livestreams))

        if self.livestreams:
            self.livestreams = list(dict.fromkeys(self.livestreams))

        streaming_data = None
        scheduled_data = None
        for video_id in set(list(self.get_video_ids_from_feed(rssdata)) + self.livestreams):
            if video_id in self.not_livestreams:
                log.debug(f"video_id in not_livestreams: {video_id}")
                continue
            log.debug(f"video_id not in not_livestreams: {video_id}")
            params = {
                "key": self._token["api_key"],
                "id": video_id,
                "part": "id,liveStreamingDetails,snippet",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(YOUTUBE_VIDEOS_ENDPOINT, params=params) as r:
                    data = await r.json()
                    try:
                        _check_api_errors(data)
                    except InvalidYoutubeCredentials:
                        log.error("The YouTube API key is either invalid or has not been set.")
                        break
                    except YoutubeQuotaExceeded:
                        log.error("YouTube quota has been exceeded.")
                        break
                    except APIError as e:
                        log.error(
                            "Something went wrong whilst trying to"
                            " contact the stream service's API.\n"
                            "Raw response data:\n%r",
                            e,
                        )
                        continue
                    video_data = data.get("items", [{}])
                    if len(video_data) > 0:
                        stream_data = video_data[0].get("liveStreamingDetails", None)
                        log.debug(f"stream_data for {video_id}: {stream_data}")
                    if (
                        len(video_data) > 0
                        and stream_data
                        and stream_data != "None"
                        and stream_data.get("actualEndTime", None) is None
                    ):
                        scheduled = stream_data.get("scheduledStartTime", None)
                        if stream_data.get("actualStartTime", None) is not None:
                            if video_id not in self.livestreams:
                                self.livestreams.append(video_id)
                            streaming_data = data
                        elif (parse_time(scheduled) - datetime.now(timezone.utc)).total_seconds() < 36000:
                            if not scheduled_data or parse_time(scheduled) < self.get_info(scheduled_data)["time"]:
                                scheduled_data = data
                    else:
                        self.not_livestreams.append(video_id)
                        if video_id in self.livestreams:
                            self.livestreams.remove(video_id)
        if scheduled_data or streaming_data:
            return scheduled_data, streaming_data
            # return await self.make_embed(embed_data)
        raise OfflineStream()

    @classmethod
    def make_embed(self, data):
        vid_data = data["items"][0]
        video_url = youtube_url_format_1.format(vid_data["id"])
        title = vid_data["snippet"]["title"]
        thumbnail = vid_data["snippet"]["thumbnails"]["medium"]["url"]
        # channel_title = vid_data["snippet"]["channelTitle"]
        embed = discord.Embed(title=title, url=video_url)
        embed.set_author(name=vid_data["snippet"]["channelTitle"])
        def rnd(url):
            """Appends a random parameter to the url to avoid Discord's caching"""
            return url + "?rnd=" + "".join([choice(ascii_letters) for _loop_counter in range(6)])
        embed.set_image(url=rnd(thumbnail))
        embed.colour = 0x9255A5
        return embed

    @classmethod
    def get_info(self, data):
        vid_data = data["items"][0]
        time = vid_data.get("liveStreamingDetails", {}).get("scheduledStartTime", None)
        return {
            "video_id": vid_data["id"],
            "channel_name": vid_data["snippet"]["channelTitle"],
            "title": vid_data["snippet"]["title"],
            "channel_id": vid_data["snippet"]["channelId"],
            "time": parse_time(time) if time else None
        }

    async def fetch_id(self):
        return await self._fetch_channel_resource("id")

    async def fetch_name(self):
        snippet = await self._fetch_channel_resource("snippet")
        return snippet["title"]

    async def _fetch_channel_resource(self, resource: str):

        params = {"key": self._token["api_key"], "part": resource}
        if resource == "id":
            params["forUsername"] = self.name
        else:
            params["id"] = self.id

        async with aiohttp.ClientSession() as session:
            async with session.get(YOUTUBE_CHANNELS_ENDPOINT, params=params) as r:
                data = await r.json()

        _check_api_errors(data)
        if "items" in data and len(data["items"]) == 0:
            raise StreamNotFound()
        elif "items" in data:
            return data["items"][0][resource]
        elif (
            "pageInfo" in data
            and "totalResults" in data["pageInfo"]
            and data["pageInfo"]["totalResults"] < 1
        ):
            raise StreamNotFound()
        raise APIError(r.status, data)

    def __repr__(self):
        return "<{0.__class__.__name__}: {0.name} (ID: {0.id})>".format(self)
    
    def get_video_ids_from_feed(self, feed):
        try:
            root = ET.fromstring(feed)
        except:
            log.warning(feed)
        else:
            rss_video_ids = []
            for child in root.iter("{http://www.w3.org/2005/Atom}entry"):
                for i in child.iter("{http://www.youtube.com/xml/schemas/2015}videoId"):
                    yield i.text

async def get_video_belong_channel(token, video_id):
    params = {
        "key": token["api_key"],
        "id": video_id,
        "part": "snippet",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(YOUTUBE_VIDEOS_ENDPOINT, params=params) as r:
            data = await r.json()
            try:
                _check_api_errors(data)
            except InvalidYoutubeCredentials:
                log.error("The YouTube API key is either invalid or has not been set.")
            except YoutubeQuotaExceeded:
                log.error("YouTube quota has been exceeded.")
            except APIError as e:
                log.error(
                    "Something went wrong whilst trying to"
                    " contact the stream service's API.\n"
                    "Raw response data:\n%r",
                    e,
                )
            else:
                video_data = data.get("items", [{}])
                if len(video_data) > 0:
                    return video_data[0].get("snippet", {}).get("channelId", None)
            return None


def _check_api_errors(data: dict):
    if "error" in data:
        error_code = data["error"]["code"]
        if error_code == 400 and data["error"]["errors"][0]["reason"] == "keyInvalid":
            raise InvalidYoutubeCredentials()
        elif error_code == 403 and data["error"]["errors"][0]["reason"] in (
            "dailyLimitExceeded",
            "quotaExceeded",
            "rateLimitExceeded",
        ):
            raise YoutubeQuotaExceeded()
        raise APIError(error_code, data)