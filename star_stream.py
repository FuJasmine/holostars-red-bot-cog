import discord
from discord.utils import get

from redbot.core.bot import Red
from redbot.core import checks, commands, Config
from redbot.core.i18n import cog_i18n, Translator, set_contextual_locales_from_guild
from redbot.core.utils._internal_utils import send_to_owners_with_prefix_replaced
from redbot.core.utils.chat_formatting import escape, pagify
from .youtube_stream import YouTubeStream, get_video_belong_channel

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
import emoji
import logging
import asyncio
import aiohttp
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Union, Dict

_ = Translator("StarStreams", __file__)
log = logging.getLogger("red.core.cogs.StarStreams")
next_message = r'{next_message}'

youtube_url_format_2 = "https://www.youtube.com/watch?v={}"
youtube_url_format_3 = "https://youtu.be/{}"

@cog_i18n(_)
class StarStream(commands.Cog):
    """Streaming bot for Holostars Chinese Fan Server.

    It will check YouTube stream and send notification.
    """

    global_defaults = {
        "refresh_timer": 60,
        "tokens": {},
        "streams": [],
        "videos": [],
    }

    guild_defaults = {
        "autodelete": False,
        "mention_everyone": False,
        "mention_here": False,
        "chat_message": None,
        "mention_message": None,
        "scheduled_message": None,
    }

    def __init__(self, bot: Red):
        super().__init__()
        self.config: Config = Config.get_conf(self, 27272727)
        self.config.register_global(**self.global_defaults)
        self.config.register_guild(**self.guild_defaults)

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
    async def stars(self, ctx: commands.Context):
        """Manage holostars discord server."""
        pass

    @stars.group(name='channel')
    async def _stars_channel(self, ctx: commands.Context):
        """Manage members' channel settings."""
        pass

    @_stars_channel.command(name="set")
    async def _channel_set(self, ctx: commands.Context, channel_name_or_id: str, mention_channel: discord.TextChannel=None, chat_channel: discord.TextChannel=None, channel_emoji: str=None):
        """Set tracking YouTube channel.
        
        Use: [p]stars channel set [YT channel id | YT channel name] [mention channel] [default chat channel] [emoji]
        """
        # if str(_emoji) == emoji for _emoji in message.guild.emojis:
        def is_emoji(word):
            return (word in emoji.UNICODE_EMOJI['en']) or (word in [str(e) for e in ctx.guild.emojis])
        if channel_emoji and not is_emoji(channel_emoji):
            await ctx.send("Emoji is not corrent")
            return
        stream = self.get_stream(channel_name_or_id)
        chat_channel_id=chat_channel.id if chat_channel else None
        mention_channel_id=mention_channel.id if mention_channel else None
        if not stream:
            token = await self.bot.get_shared_api_tokens(YouTubeStream.token_name)
            if not self.check_name_or_id(channel_name_or_id):
                stream = YouTubeStream(
                    _bot=self.bot, id=channel_name_or_id, token=token
                    , config=self.config
                    , chat_channel_id=chat_channel_id
                    , mention_channel_id=mention_channel_id
                    , emoji=channel_emoji
                )
            else:
                stream = YouTubeStream(
                    _bot=self.bot, name=channel_name_or_id, token=token
                    , config=self.config
                    , chat_channel_id=chat_channel_id
                    , mention_channel_id=mention_channel_id
                    , emoji=channel_emoji
                )
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
                # add stream
                self.streams.append(stream)
                await ctx.send(
                    _(
                        "I'll now send a notification in this channel when {stream.name} is live."
                    ).format(stream=stream)
                )

        else:
            # update stream
            self.streams.remove(stream)
            if chat_channel:
                stream.chat_channel_id = chat_channel_id
            if mention_channel:
                stream.mention_channel_id = mention_channel_id
            stream.emoji = channel_emoji
            self.streams.append(stream)
            await ctx.send(
                _(
                    "I have already updated {stream.name} settings."
                ).format(stream=stream)
            )
            
        await self.save_streams()

    @_stars_channel.command(name="unset")
    async def _channel_unset(self, ctx: commands.Context, channel_name_or_id_or_all: str):
        """Unset tracking YouTube channel.
        
        Use: [p]stars channel unset [YT channel id | YT channel name | all]
        """
        async def delete_stream(stream):
            self.streams.remove(stream)
            await ctx.send(
                _(
                    "I won't send notifications about {stream.name} in this channel anymore."
                ).format(stream=stream)
            )
        if channel_name_or_id_or_all == "all":
            while len(self.streams) > 0:
                await delete_stream(self.streams[0])
        else:
            stream = self.get_stream(channel_name_or_id_or_all)
            if not stream:
                await ctx.send(
                    _(
                        "It's not exist."
                    ).format(stream=stream)
                )
            else:
                await delete_stream(stream)
        await self.save_streams()

    @_stars_channel.command(name="list")
    async def _stars_channel_list(self, ctx: commands.Context):
        """List all active stream alerts in this server."""
        streams_list = defaultdict(list)
        guild_channels_ids = [c.id for c in ctx.guild.channels]
        msg = _("Active alerts:\n\n")

        if len(self.streams) == 0:
            await ctx.send(_("There are no active alerts in this server."))
            return

        for stream in self.streams:
            msg += f"**{stream.name}**\n"
            if stream.mention_channel_id:
                msg += f" - mention channel: `#{ctx.guild.get_channel(stream.mention_channel_id)}`\n"
            if stream.chat_channel_id:
                msg += f" - chat channel: `#{ctx.guild.get_channel(stream.chat_channel_id)}`\n"
            if len(stream.mention) > 0:
                roles_str = ', '.join([f'`@{get(ctx.guild.roles, id=role_id).name}`' for role_id in stream.mention])
                msg += f" - mention roles: {roles_str}\n"

        for page in pagify(msg):
            await ctx.send(page)

    @stars.group(name='set')
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


    @_stars_channel.command(name="mention")
    async def _stars_mention(self, ctx: commands.Context, yt_channel_id_or_name: str, role: discord.Role):
        """Set mention role in each channel
        Use stars mention [channel id | channel name] [role]
        """
        stream = self.get_stream(yt_channel_id_or_name)
        if not stream:
            await ctx.send(f"`{yt_channel_id_or_name}` is not found")
            return
        if role.id not in stream.mention:
            stream.mention.append(role.id)
        else:
            stream.mention.remove(role.id)

        if len(stream.mention) > 0:
            await ctx.send(
                _(
                    f'I will send mention `{", ".join([get(ctx.guild.roles, id=role_id).name for role_id in stream.mention])}`.'
                )
            )
        else:
            await ctx.send(
                _(
                    "No mention role in this channel."
                ).format(stream=stream)
            )
        await self.save_streams()

    @stars.group(name="stream")
    async def _stars_stream(self, ctx: commands.Context):
        """Mange stream
        """

    @_stars_stream.command(name="add")
    async def _stream_add(self, ctx: commands.Context, stream_id: str, chat_channel: discord.TextChannel=None):
        """ Add stream
        If not assign **chat channel**, it will set by yotube channel settings.
        Use: [p]stars stream add [YT stream id] <[chat channel]>
        """
        if chat_channel:
            # TODO
            pass
        else:
            token = await self.bot.get_shared_api_tokens(YouTubeStream.token_name)
            yt_channel_id = await get_video_belong_channel(token, stream_id)
            if yt_channel_id:
                stream = self.get_stream(yt_channel_id)
                if stream:
                    if stream_id not in stream.livestreams:
                        stream.livestreams.append(stream_id)
                        await self.save_streams()
                        await ctx.send(f"`{stream_id}` will be alert at `{yt_channel_id}`.")
                else:
                    await ctx.send(f"`{yt_channel_id}`` has not set.")
            else:
                await ctx.send(f"`{stream_id}`` is not found.")

    @_stars_stream.command(name="check")
    #TODO: limit time
    async def _stream_check(self, ctx: commands.Context):
        """ 
        
        """
        await self.check_streams()

    async def _stream_alerts(self):
        await self.bot.wait_until_ready()
        while True:
            await self.check_streams()
            await asyncio.sleep(await self.config.refresh_timer())
    
    async def _send_stream_alert(
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
            content = content.split(next_message)
            m = await channel.send(
                content[0],
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
            )
            ms = [m]
            for i in range(1, len(content)):
                time.sleep(2)
                m = await channel.send(
                    content[i],
                    allowed_mentions=discord.AllowedMentions(roles=True, everyone=True),
                )
                ms.append(m)
            return ms
    
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

    @stars.group()
    @commands.guild_only()
    async def message(self, ctx: commands.Context):
        """Manage custom messages for stream alerts."""
        pass

    @message.command(name='chat')
    async def _message_chat(self, ctx: commands.Context, *, message: str):
        guild = ctx.guild
        await self.config.guild(guild).chat_message.set(message)
        await ctx.send(_("Stream alert message set!"))

    @message.command(name='mention')
    async def _message_mention(self, ctx: commands.Context, *, message: str):
        guild = ctx.guild
        await self.config.guild(guild).mention_message.set(message)
        await ctx.send(_("Stream alert message set!"))

    @message.command(name='scheduled')
    async def _message_schduled(self, ctx: commands.Context, *, message: str):
        guild = ctx.guild
        await self.config.guild(guild).scheduled_message.set(message)
        await ctx.send(_("Stream alert message set!"))

    async def check_streams(self):
        # TODO: continue when video in video
        to_remove = []
        for stream in self.streams:
            try:
                try:
                    scheduled_data, streaming_data = await stream.is_online()
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
                    # alert_msg = await self.config.guild(channel.guild).live_message_mention()
                    changed = False
                    if scheduled_data:
                        info = YouTubeStream.get_info(scheduled_data)
                        video_id = info["video_id"]
                        if stream.chat_channel_id and video_id not in stream.scheduled_sent:
                            channel = self.bot.get_channel(stream.chat_channel_id)
                            content = await self.config.guild(channel.guild).scheduled_message()
                            await self.send_scheduled(
                                stream.mention_channel_id, info, pin=True, content=content
                            )
                            stream.scheduled_sent.append(video_id)
                            changed = True
                    if streaming_data:
                        video_id = YouTubeStream.get_info(streaming_data)["video_id"]
                        if video_id not in stream.streaming_sent:
                            if stream.mention_channel_id:
                                channel = self.bot.get_channel(stream.mention_channel_id)
                                content = await self.config.guild(channel.guild).mention_message()
                                await self.send_streaming(
                                    streaming_data, stream.mention_channel_id, 
                                    is_mention=True, embed=True, 
                                    chat_channel_id=stream.chat_channel_id, 
                                    content=content # "{channel_name} is live! {chat_channel}"
                                )
                            if stream.chat_channel_id:
                                channel = self.bot.get_channel(stream.chat_channel_id)
                                content = await self.config.guild(channel.guild).chat_message()
                                await self.send_streaming(
                                    streaming_data, stream.chat_channel_id,
                                    is_mention=False, embed=False, 
                                    content=content
                                )
                            stream.streaming_sent.append(video_id)
                            changed = True
                    if changed:
                        await self.save_streams()
            except Exception as e:
                log.error("An error has occured with Streams. Please report it.", exc_info=e)

        if to_remove:
            for stream in to_remove:
                self.streams.remove(stream)
            await self.save_streams()

    async def send_streaming(
        self, data, channel_id, is_mention, content=None
        , embed=False, description=None, pin=False, chat_channel_id=None
        ):
        embed = YouTubeStream.make_embed(data) if embed else None
        info = YouTubeStream.get_info(data)
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        if await self.bot.cog_disabled_in_guild(self, channel.guild):
            return
        await set_contextual_locales_from_guild(self.bot, channel.guild)

        mention_str, edited_roles = (await self._get_mention_str(
            channel.guild, channel, [info["channel_id"]]
        )) if is_mention else ("", [])
        
        url = youtube_url_format_2.format(info["video_id"])
        if not content:
            content = content.replace("{title}", info["title"])
        content = content.replace("{channel_name}", info["channel_name"])
        content = content.replace("{url}", url)
        content = content.replace("{mention}", mention_str)
        content = content.replace("{description}", description if description else info["title"])
        content = content.replace("{new_line}", "\n")
        if chat_channel_id:
            chat_channel = self.bot.get_channel(chat_channel_id)
            if chat_channel:
                content = content.replace("{chat_channel}", chat_channel.mention)
        ms = await self._send_stream_alert(channel, embed, content)
        if pin:
            await ms[0].pin()

    async def send_scheduled(self, channel_id, info=None, content=None, description=None, pin=False):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        if await self.bot.cog_disabled_in_guild(self, channel.guild):
            return
        await set_contextual_locales_from_guild(self.bot, channel.guild)

        url = youtube_url_format_2.format(info["video_id"])
        if not content:
            content = "{time}\n{description}\n{url}"
        content = content.replace("{title}", info["title"])
        content = content.replace("{channel_name}", info["channel_name"])
        content = content.replace("{url}", url)
        content = content.replace("{description}", description if description else info["title"])
        if info["time"]:
            content = content.replace("{time}", getDiscordTimeStamp(info["time"]))
        ms = await self._send_stream_alert(channel, None, content)
        if pin:
            await ms[0].pin()
    
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
            role_ids += self.get_stream(ch_id).mention
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
    
def getTimeType(date_str):
    if date_str == "":
        return datetime.now(timezone.utc)
    return datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%SZ')

def getDiscordTimeStamp(date):
    return "<t:{}:f>".format(int(time.mktime(date.timetuple()))-time.timezone)
