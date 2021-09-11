import discord
from redbot.core.bot import Red
from redbot.core import checks, commands, Config
from discord.utils import get
from redbot.core.i18n import cog_i18n, Translator, set_contextual_locales_from_guild
from redbot.core.utils._internal_utils import send_to_owners_with_prefix_replaced
from redbot.core.utils.chat_formatting import escape, pagify

from .errors import (
    APIError,
    InvalidYoutubeCredentials,
    OfflineStream,
    StreamNotFound,
    StreamsError,
    YoutubeQuotaExceeded,
)

import re
import time
import logging
import asyncio
import aiohttp
from random import choice
from string import ascii_letters
import xml.etree.ElementTree as ET
from typing import Optional, List, Tuple, Union, Dict

_ = Translator("StarStreams", __file__)
log = logging.getLogger("red.core.cogs.StarStreams")
new_line = r'{new_line}'

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
YOUTUBE_CHANNELS_ENDPOINT = YOUTUBE_BASE_URL + "/channels"
YOUTUBE_SEARCH_ENDPOINT = YOUTUBE_BASE_URL + "/search"
YOUTUBE_VIDEOS_ENDPOINT = YOUTUBE_BASE_URL + "/videos"
YOUTUBE_CHANNEL_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

youtube_url_format_1 = "https://youtube.com/watch?v={}"
youtube_url_format_2 = "https://www.youtube.com/watch?v={}"
youtube_url_format_3 = "https://youtu.be/{}"


class YouTubeStream():
    token_name = "youtube"
    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        self._token = kwargs.pop("token", None)
        self._config = kwargs.pop("config")
        self.not_livestreams: List[str] = []
        self.livestreams: List[str] = []

        self._bot = kwargs.pop("_bot")
        self.name = kwargs.pop("name", None)
        self.channels = kwargs.pop("channels", [])
        # self.already_online = kwargs.pop("already_online", False)
        self.messages = kwargs.pop("messages", [])
        self.type = self.__class__.__name__
        self._youtube_channel_re = re.compile(r"(https?://(www.youtube.com|m.youtube.com|youtube.com|youtu.be/)/channel/(UC[-_A-Za-z0-9]{21}[AQgw]))")

    @property
    def display_name(self) -> Optional[str]:
        return self.name

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

        embed_data = None
        for video_id in self.get_video_ids_from_feed(rssdata):
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
                        self._check_api_errors(data)
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
                    if len(video_data) == 0:
                        continue
                    video_data = video_data[0]
                    stream_data = video_data.get("liveStreamingDetails", {})
                    log.debug(f"stream_data for {video_id}: {stream_data}")
                    if (
                        stream_data
                        and stream_data != "None"
                        and stream_data.get("actualEndTime", None) is None
                    ):
                        if stream_data.get("actualStartTime", None) is not None:
                            if video_id not in self.livestreams:
                                self.livestreams.append(video_id)
                            embed_data = data
                    else:
                        self.not_livestreams.append(video_id)
                        if video_id in self.livestreams:
                            self.livestreams.remove(video_id)
        if embed_data is not None:
            return await self.make_embed(embed_data)
        log.debug(f"livestreams for {self.name}: {self.livestreams}")
        log.debug(f"not_livestreams for {self.name}: {self.not_livestreams}")
        raise OfflineStream()

    async def make_embed(self, data):
        vid_data = data["items"][0]
        video_url = youtube_url_format_1.format(vid_data["id"])
        title = vid_data["snippet"]["title"]
        thumbnail = vid_data["snippet"]["thumbnails"]["medium"]["url"]
        channel_title = vid_data["snippet"]["channelTitle"]
        embed = discord.Embed(title=title, url=video_url)
        embed.set_author(name=channel_title)
        embed.set_image(url=rnd(thumbnail))
        embed.colour = 0x9255A5
        info = {}
        info["video_id"] = vid_data["id"]
        description = vid_data["snippet"]["description"]
        ch_ids = [link[-1] for link in self._youtube_channel_re.findall(description)]
        ch_ids.append(vid_data["snippet"]["channelId"])
        info["ch_ids"] = list(set(ch_ids))
        return embed, info

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

        self._check_api_errors(data)
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

    def _check_api_errors(self, data: dict):
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


@cog_i18n(_)
class StarStream(commands.Cog):
    """Streaming bot for Holostars Chinese Fan Server.

    It will check YouTube stream and send notification.
    """
    youtube_channel_config = "YOUTUBE_CHANNEL"
    youtube_video_config = "YOUTUBE_VIDEO"

    global_defaults = {
        "refresh_timer": 60,
        "tokens": {},
        "streams": [],
    }

    guild_defaults = {
        "autodelete": False,
        "mention_everyone": False,
        "mention_here": False,
        "live_message_mention": False,
        "live_message_nomention": False
    }

    channel_defaults = {
        "mention": [],
        "emoji": "",
    }

    video_defaults = {
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.config: Config = Config.get_conf(self, 27272727)
        self.config.register_global(**self.global_defaults)
        self.config.register_guild(**self.guild_defaults)
        self.config.init_custom(self.youtube_channel_config, 1)
        self.config.register_custom(self.youtube_channel_config, **self.channel_defaults)
        self.config.init_custom(self.youtube_video_config, 1)
        self.config.register_custom(self.youtube_video_config, **self.channel_defaults)

        self.bot: Red = bot

        self.streams: List[Stream] = []
        self.task: Optional[asyncio.Task] = None

        self._ready_event: asyncio.Event = asyncio.Event()
        self._init_task: asyncio.Task = self.bot.loop.create_task(self.initialize())
        self._youtube_video_re = re.compile(r"https?://(www.youtube.com/watch\?v=|youtube.com/watch\?v=|m.youtube.com/watch\?v=|youtu.be/)([0-9A-Za-z_-]{10}[048AEIMQUYcgkosw])")
    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    def check_name_or_id(self, data: str) -> bool:
        channel_id_re = re.compile("^UC[-_A-Za-z0-9]{21}[AQgw]$")
        matched = channel_id_re.fullmatch(data)
        if matched is None:
            return True
        return False
    
    async def initialize(self) -> None:
        """Should be called straight after cog instantiation."""
        await self.bot.wait_until_ready()

        try:
            await self.move_api_keys()
            self.streams = await self.load_streams()
            self.task = self.bot.loop.create_task(self._stream_alerts())
        except Exception as error:
            log.exception("Failed to initialize Streams cog:", exc_info=error)

        self._ready_event.set()

    async def cog_before_invoke(self, ctx: commands.Context):
        await self._ready_event.wait()

    async def move_api_keys(self) -> None:
        """Move the API keys from cog stored config to core bot config if they exist."""
        tokens = await self.config.tokens()
        youtube = await self.bot.get_shared_api_tokens("youtube")
        for token_type, token in tokens.items():
            if "api_key" not in youtube:
                await self.bot.set_shared_api_tokens("youtube", api_key=token)
        await self.config.tokens.clear()
    
    @commands.group()
    @commands.guild_only()
    @checks.mod_or_permissions(manage_channels=True)
    async def starsalert(self, ctx: commands.Context):
        """Manage stars' stream alerts."""
        pass

    @starsalert.command(name="add")
    async def _starsalert_add(self, ctx: commands.Context, channel_name_or_id: str):
        """Toggle alerts in this channel for a YouTube stream.
        
        Use: [p]stars alert [channel id or name]
        """
        stream = self.get_stream(channel_name_or_id)
        if not stream:
            token = await self.bot.get_shared_api_tokens(YouTubeStream.token_name)
            if not self.check_name_or_id(channel_name_or_id):
                stream = YouTubeStream(_bot=self.bot, id=channel_name_or_id, token=token, config=self.config)
            else:
                stream = YouTubeStream(_bot=self.bot, name=channel_name_or_id, token=token, config=self.config)
            try:
                exists = await stream.check_exists()
            except InvalidYoutubeCredentials:
                await ctx.send(
                    _(
                        "The YouTube API key is either invalid or has not been set. See "
                        "{command}."
                    ).format(command=f"`{ctx.clean_prefix}streamset youtubekey`")
                )
                return
            except YoutubeQuotaExceeded:
                await ctx.send(
                    _(
                        "YouTube quota has been exceeded."
                        " Try again later or contact the owner if this continues."
                    )
                )
            except APIError as e:
                log.error(
                    "Something went wrong whilst trying to contact the stream service's API.\n"
                    "Raw response data:\n%r",
                    e,
                )
                await ctx.send(
                    _("Something went wrong whilst trying to contact the stream service's API.")
                )
                return
            else:
                if not exists:
                    await ctx.send(_("That channel doesn't seem to exist."))
                    return

        await self.add_or_remove(ctx, stream)

    @starsalert.command(name="stop", usage="[disable_all=No]")
    async def _starsalert_stop(self, ctx: commands.Context, _all: bool = False):
        """Disable all stream alerts in this channel or server.

        `[p]streamalert stop` will disable this channel's stream
        alerts.

        Do `[p]streamalert stop yes` to disable all stream alerts in
        this server.
        """
        streams = self.streams.copy()
        local_channel_ids = [c.id for c in ctx.guild.channels]
        to_remove = []

        for stream in streams:
            for channel_id in stream.channels:
                if channel_id == ctx.channel.id:
                    stream.channels.remove(channel_id)
                elif _all and ctx.channel.id in local_channel_ids:
                    if channel_id in stream.channels:
                        stream.channels.remove(channel_id)

            if not stream.channels:
                to_remove.append(stream)

        for stream in to_remove:
            streams.remove(stream)

        self.streams = streams
        await self.save_streams()

        if _all:
            msg = _("All the stream alerts in this server have been disabled.")
        else:
            msg = _("All the stream alerts in this channel have been disabled.")

        await ctx.send(msg)

    @starsalert.command(name="list")
    async def _starsalert_list(self, ctx: commands.Context):
        """List all active stream alerts in this server."""
        streams_list = defaultdict(list)
        guild_channels_ids = [c.id for c in ctx.guild.channels]
        msg = _("Active alerts:\n\n")

        for stream in self.streams:
            for channel_id in stream.channels:
                if channel_id in guild_channels_ids:
                    streams_list[channel_id].append(stream.name.lower())

        if not streams_list:
            await ctx.send(_("There are no active alerts in this server."))
            return

        for channel_id, streams in streams_list.items():
            channel = ctx.guild.get_channel(channel_id)
            msg += "** - #{}**\n{}\n".format(channel, ", ".join(streams))

        for page in pagify(msg):
            await ctx.send(page)

    @commands.group()
    @checks.mod_or_permissions(manage_channels=True)
    async def starsset(self, ctx: commands.Context):
        """Manage stream alert settings."""
        pass

    @starsset.command(name="timer")
    @checks.is_owner()
    async def _starsset_refresh_timer(self, ctx: commands.Context, refresh_time: int):
        """Set stream check refresh time."""
        if refresh_time < 60:
            return await ctx.send(_("You cannot set the refresh timer to less than 60 seconds"))

        await self.config.refresh_timer.set(refresh_time)
        await ctx.send(
            _("Refresh timer set to {refresh_time} seconds".format(refresh_time=refresh_time))
        )

    @starsset.command()
    @checks.is_owner()
    async def youtubekey(self, ctx: commands.Context):
        """Explain how to set the YouTube token."""

        message = _(
            "To get one, do the following:\n"
            "1. Create a project\n"
            "(see https://support.google.com/googleapi/answer/6251787 for details)\n"
            "2. Enable the YouTube Data API v3 \n"
            "(see https://support.google.com/googleapi/answer/6158841 for instructions)\n"
            "3. Set up your API key \n"
            "(see https://support.google.com/googleapi/answer/6158862 for instructions)\n"
            "4. Copy your API key and run the command "
            "{command}\n\n"
            "Note: These tokens are sensitive and should only be used in a private channel\n"
            "or in DM with the bot.\n"
        ).format(
            command="`{}set api youtube api_key {}`".format(
                ctx.clean_prefix, _("<your_api_key_here>")
            )
        )

        await ctx.maybe_send_embed(message)

    @commands.group()
    @checks.mod_or_permissions(manage_channels=True)
    async def stars(self, ctx: commands.Context):
        """Manage stars data."""
        pass

    @stars.command(name="mention")
    async def _stars_mention(self, ctx: commands.Context, ch_id: str, role: discord.Role):
        """Set mention role in each channel
        Use stars mention [channel id] [role]
        """
        if self.check_name_or_id(ch_id):
            await ctx.send(
                _(
                    "`{ch_id}` is not channel id"
                ).format(ch_id=ch_id)
            )
            return
        roles = list(await self.config.custom(self.youtube_channel_config, ch_id).mention())
        if role.id not in roles:
            roles.append(role.id)
        else:
            roles.remove(role.id)

        if len(roles) > 0:
            await ctx.send(
                _(
                    "I will send mention `{roles}`."
                ).format(roles=", ".join([get(ctx.guild.roles, id=role_id).name for role_id in roles]))
            )
        else:
            await ctx.send(
                _(
                    "No any mention role in this channel."
                ).format(stream=stream)
            )
        await self.config.custom(self.youtube_channel_config, ch_id).mention.set(roles)

    @stars.command(name="stream")
    async def _stars_stream(self, ctx: commands.Context, yotube_links: str):
        video_ids = [info[-1] for info in self._youtube_video_re.findall(yotube_links)]
        videos = list(await self.config.videos())
        # TODO

    async def _stream_alerts(self):
        await self.bot.wait_until_ready()
        while True:
            await self.check_streams()
            await self.check_videos()
            await asyncio.sleep(await self.config.refresh_timer())
    
    async def _send_stream_alert(
        self,
        stream,
        channel: discord.TextChannel,
        embed: discord.Embed,
        content: str = None,
    ):
        if content == None:
            m = await channel.send(
                None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
            )
        else:
            content = content.split(new_line)
            if len(content) == 1:
                m = await channel.send(
                    content[0],
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
                )
            else:
                for c in content:
                    m = await channel.send(
                        c,
                        # embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
                    )
                    time.sleep(3)
        message_data = {"guild": m.guild.id, "channel": m.channel.id, "message": m.id}
        stream.messages.append(message_data)
    
    async def _send_video_alert(
        self,
        channel: discord.TextChannel,
        embed: discord.Embed,
        content: str = None,
    ):
        if content == None:
            m = await channel.send(
                None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
            )
        else:
            content = content.split(new_line)
            if len(content) == 1:
                m = await channel.send(
                    content[0],
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
                )
            else:
                for c in content:
                    m = await channel.send(
                        c,
                        # embed=embed,
                        allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
                    )
                    time.sleep(3)
        # TODO: message_data = {"guild": m.guild.id, "channel": m.channel.id, "message": m.id}
        # stream.messages.append(message_data)

    async def check_streams(self):
        # TODO: continue when video in video
        to_remove = []
        for stream in self.streams:
            try:
                try:
                    embed, info = await stream.is_online()
                except StreamNotFound:
                    log.info("Stream with name %s no longer exists", stream.name)
                    continue
                except OfflineStream:
                    if not stream.messages:
                        continue
                    stream.messages.clear()
                    await self.save_streams()
                except APIError as e:
                    log.error(
                        "Something went wrong whilst trying to contact the stream service's API.\n"
                        "Raw response data:\n%r",
                        e,
                    )
                    continue
                else:
                    if stream.messages:
                        continue
                    for channel_id in stream.channels:
                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            continue
                        if await self.bot.cog_disabled_in_guild(self, channel.guild):
                            continue
                        await set_contextual_locales_from_guild(self.bot, channel.guild)

                        mention_str, edited_roles = await self._get_mention_str(
                            channel.guild, channel, info["ch_ids"]
                        )
                        
                        url = youtube_url_format_2.format(info["video_id"])
                        if mention_str:
                            alert_msg = await self.config.guild(
                                channel.guild
                            ).live_message_mention()
                            if alert_msg:
                                content = alert_msg  # Stop bad things from happening here...
                                content = content.replace(
                                    "{stream.name}", str(stream.name)
                                )  # Backwards compatibility
                                content = content.replace(
                                    "{stream.display_name}", str(stream.display_name)
                                )
                                content = content.replace("{stream}", str(stream.name))
                                content = content.replace("{url}", url)
                                content = content.replace("{mention}", mention_str)
                            else:
                                content = _("{mention}, {display_name} is live!").format(
                                    mention=mention_str,
                                    display_name=escape(
                                        str(stream.display_name),
                                        mass_mentions=True,
                                        formatting=True,
                                    ),
                                )
                        else:
                            alert_msg = await self.config.guild(
                                channel.guild
                            ).live_message_nomention()
                            if alert_msg:
                                content = alert_msg  # Stop bad things from happening here...
                                content = content.replace(
                                    "{stream.name}", str(stream.name)
                                )  # Backwards compatibility
                                content = content.replace(
                                    "{stream.display_name}", str(stream.display_name)
                                )
                                content = content.replace("{stream}", str(stream.name))
                                content = content.replace("{url}", url)
                            else:
                                content = _("{display_name} is live!").format(
                                    display_name=escape(
                                        str(stream.display_name),
                                        mass_mentions=True,
                                        formatting=True,
                                    )
                                )
                        await self._send_stream_alert(stream, channel, embed, content)
            except Exception as e:
                log.error("An error has occured with Streams. Please report it.", exc_info=e)

        if to_remove:
            for stream in to_remove:
                self.streams.remove(stream)
            await self.save_streams()

    async def check_videos(self):
        data = {
            864755730677497876: {
                "videos": ["KXTWWhl6PJ4", "iD1Hho1GBjY"],
                "messa ges": []
            }
        }
        for channel_id, channel_info in data.items():
            for video in channel_info["videos"]:
                try:
                    try:
                        embed = None
                        info = {
                            "ch_ids": ["UCNVEsYbiZjH5QLmGeSgTSzg"],
                            "name": ["astel ch.アステル"]
                        }
                        #emself.video_is_online(video)
                    except OfflineStream:
                        if not channel_info["messages"]:
                            continue
                        channel_info["messages"] = ""
                        # TODO: save
                    except APIError as e:
                        log.error(
                            "Something went wrong whilst trying to contact the stream service's API.\n"
                            "Raw response data:\n%r",
                            e,
                        )
                        continue
                    else:
                        if channel_info["messages"]:
                            continue
                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            continue
                        if await self.bot.cog_disabled_in_guild(self, channel.guild):
                            continue
                        await set_contextual_locales_from_guild(self.bot, channel.guild)

                        mention_str, edited_roles = await self._get_mention_str(
                            channel.guild, channel, info["ch_ids"]
                        )

                        url = youtube_url_format_2.format(video)
                        if mention_str:
                            alert_msg = await self.config.guild(
                                channel.guild
                            ).live_message_mention()
                            if alert_msg:
                                content = alert_msg  # Stop bad things from happening here...
                                content = content.replace("{url}", url)
                                content = content.replace("{mention}", mention_str)
                            else:
                                content = _("{mention}, {display_name} is live!").format(
                                    mention=mention_str,
                                    display_name=info["name"]
                                )
                        else:
                            alert_msg = await self.config.guild(
                                channel.guild
                            ).live_message_nomention()
                            if alert_msg:
                                content = alert_msg  # Stop bad things from happening here...
                                content = content.replace("{url}", url)
                            else:
                                content = _("{display_name} is live!").format(
                                    display_name=info["name"]
                                )
                        await self._send_video_alert(channel, embed, content)
                except Exception as e:
                    log.error("An error has occured with Streams. Please report it.", exc_info=e)

    async def video_is_online(self, video):
        pass    
    async def _get_mention_str(
        self, guild: discord.Guild, channel: discord.TextChannel, ch_ids: list
    ) -> Tuple[str, List[discord.Role]]:
        """Returns a 2-tuple with the string containing the mentions, and a list of
        all roles which need to have their `mentionable` property set back to False.
        """
        settings = self.config.guild(guild)
        mentions = []
        edited_roles = []
        if await settings.mention_everyone():
            mentions.append("@everyone")
        if await settings.mention_here():
            mentions.append("@here")
            
        can_mention_everyone = channel.permissions_for(guild.me).mention_everyone
        role_ids = []
        for ch_id in ch_ids:
            role_ids += list(await self.config.custom(self.youtube_channel_config, ch_id).mention())
        role_ids = list(set(role_ids))
        for role_id in role_ids:
            role =  get(guild.roles, id=role_id)
            if not can_mention_everyone and can_manage_roles and not role.mentionable:
                try:
                    await role.edit(mentionable=True)
                except discord.Forbidden:
                    # Might still be unable to edit role based on hierarchy
                    pass
                else:
                    edited_roles.append(role)
            mentions.append(role.mention)
        return " ".join(mentions), edited_roles

    async def load_streams(self):
        streams = []
        for raw_stream in await self.config.streams():
            token = await self.bot.get_shared_api_tokens(YouTubeStream.token_name)
            if token:
                raw_stream["config"] = self.config
                raw_stream["token"] = token
            raw_stream["_bot"] = self.bot
            streams.append(YouTubeStream(**raw_stream))

        return streams
    
    async def save_streams(self):
        raw_streams = []
        for stream in self.streams:
            raw_streams.append(stream.export())

        await self.config.streams.set(raw_streams)

    def cog_unload(self):
        if self.task:
            self.task.cancel()

    def get_stream(self, name):
        for stream in self.streams:
            if self.check_name_or_id(name) and stream.name.lower() == name.lower():
                return stream
            elif not self.check_name_or_id(name) and stream.id == name:
                return stream
        return None
    
    async def add_or_remove(self, ctx: commands.Context, stream):
        if ctx.channel.id not in stream.channels:
            stream.channels.append(ctx.channel.id)
            if stream not in self.streams:
                self.streams.append(stream)
            await ctx.send(
                _(
                    "I'll now send a notification in this channel when {stream.name} is live."
                ).format(stream=stream)
            )
        else:
            stream.channels.remove(ctx.channel.id)
            if not stream.channels:
                self.streams.remove(stream)
            await ctx.send(
                _(
                    "I won't send notifications about {stream.name} in this channel anymore."
                ).format(stream=stream)
            )

        await self.save_streams()
    
def rnd(url):
    """Appends a random parameter to the url to avoid Discord's caching"""
    return url + "?rnd=" + "".join([choice(ascii_letters) for _loop_counter in range(6)])
