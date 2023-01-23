"""
    DiscordNPC lets you interact with ChatGPT through a Discord voice channel.
    Copyright (C) 2023  Parker Wahle

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
# from __future__ import annotations  breaks pycord slash command type inference

from logging import Logger, getLogger
from threading import Lock
from time import sleep as blocking_sleep
from typing import Callable, Awaitable, cast

from discord import Bot, Embed, slash_command, ApplicationContext, VoiceState, Member
from discord.ext.commands import Cog
from discord.sinks import Sink
from google_speech import Speech
from revChatGPT.ChatGPT import Chatbot

from .async_helpers import make_async
from .chatgpt_types import Answer
from .peppercord_audio import CustomVoiceClient, EnhancedFFmpegPCMAudioBytesTransformed, EnhancedSource
from .sinks import AssemblyAITranscriptionSink

logger: Logger = getLogger(__name__)

GUILD_IDS: list[int] | None = [919622423677136986, 383003210241277952]  # change these to your guilds

BOT_ACKNOWLEDGE_SPEECH: str = "I heard you say \"{speech}\". Give me a second to think..."
RATELIMIT_SPEECH: str = "I lost my train of thought. Give me a minute to get back on track..."

TTS_SPEEDUP_RATE: float = 2.0


def modify_text_to_speech_audio(mp3_audio_in: bytes) -> bytes:
    # TODO
    return mp3_audio_in


def speak(client: CustomVoiceClient, text: str) -> None:
    logger.info(f"Speaking: {text}")

    speech: Speech = Speech(text, "en")

    segment_bytes: list[bytes] = [segment.getAudioData() for segment in speech]

    transformed_bytes: list[bytes] = [modify_text_to_speech_audio(segment) for segment in segment_bytes]

    sources: list[EnhancedSource] = [EnhancedFFmpegPCMAudioBytesTransformed.from_bytes(speech_bytes) for speech_bytes in
                                     transformed_bytes]

    for source in sources:
        client.queue.put_nowait(source)


def make_talk_callable(client: CustomVoiceClient) -> Callable[[str], None]:
    return lambda speech: speak(client, speech)


class ChatGPTCog(Cog):
    def __init__(self, bot: Bot, chatbot_factory: Callable[[], Awaitable[Chatbot]], assembly_key: str) -> None:
        self.bot: Bot = bot
        self.chatbot_factory: Callable[[], Awaitable[Chatbot]] = chatbot_factory
        self.assembly_key: str = assembly_key
        self.chatbot: Chatbot | None = None
        self._sync_chatbot_lock: Lock = Lock()

    @Cog.listener()
    async def on_ready(self) -> None:
        # py-cord sucks discord.py does this better
        self.chatbot: Chatbot = await self.chatbot_factory()
        logger.info("Chatbot is ready.")

    @Cog.listener("on_voice_state_update")  # ported from regulad/PepperCord
    async def on_left_alone(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        if (
                member.guild.voice_client is not None
                and member.guild.voice_client.channel == before.channel
        ):
            if len(before.channel.members) == 1:
                await member.guild.voice_client.disconnect(force=False)

    def ask_with_refresh(self, *args, **kwargs) -> Answer | None:
        args_copy: tuple = args
        kwargs_copy: dict = kwargs

        callback_on_extra_time: Callable[[], None] | None = kwargs.pop("callback_on_extra_time", None)

        with self._sync_chatbot_lock:
            logger.info(f"Asking the Chatbot a question: {args[0]}")

            if self.chatbot is None:
                raise RuntimeError("Chatbot is not ready!")

            if "conversation_id" in kwargs and kwargs["conversation_id"] is None:  # poor handling in library
                self.chatbot.conversation_id = None

            try:
                maybe_answer: Answer | None = self.chatbot.ask(*args, **kwargs)
                if maybe_answer is None:
                    raise RuntimeError("Chatbot returned None!")
                else:
                    logger.info(f"Chatbot answered: {maybe_answer['message']}")
                return maybe_answer
            except Exception as error:
                logger.exception(f"Chatbot failed to answer: {error}")
                if str(error) == "Wrong response code! Refreshing session...":
                    if callback_on_extra_time is not None:
                        callback_on_extra_time()
                    blocking_sleep(60)
                    return self.ask_with_refresh(*args_copy, **kwargs_copy)  # recurse!!!!! recurse!!!!

    def async_ask_with_refresh(self, *args, **kwargs) -> Awaitable[Answer]:
        return make_async(self.ask_with_refresh)(*args, **kwargs)

    def make_speech_handler(self, client: CustomVoiceClient, conversation_id: str) -> Callable[[str], None]:
        talk: Callable[[str], None] = make_talk_callable(client)
        ratelimited: Callable[[], None] = lambda: talk(RATELIMIT_SPEECH)

        def speech_handler(speech: str) -> None:
            talk(BOT_ACKNOWLEDGE_SPEECH.format(speech=speech))

            answer: Answer | None = None
            while answer is None:
                answer = self.ask_with_refresh(
                    speech,
                    conversation_id=conversation_id,
                    callback_on_extra_time=ratelimited
                )
                if answer is not None:
                    ratelimited()
            talk(answer["message"])

        return speech_handler

    @slash_command(guild_ids=GUILD_IDS)
    async def ask(self, ctx: ApplicationContext, prompt: str, conversation_id: str | None = None) -> None:
        """
        Asks ChatGPT a simple question in a new conversation.
        :param ctx: The context of the slash command.
        :param prompt: The question to ask the chatbot. If you specify a conversation ID, this will be the response to the previous question.
        :param conversation_id:  A UUID of a conversation ID. This will have been returned in a previous message.
        """

        await ctx.interaction.response.defer()

        await ctx.bot.wait_until_ready()  # we have a hook on ready, so we need to wait for this

        if conversation_id is not None:
            try:
                assert conversation_id.replace("-", "").isalnum(), "Conversation ID must be a valid UUID."
            except AssertionError:
                await ctx.interaction.followup.send(
                    embed=Embed(
                        title="Invalid Conversation ID",
                        description="Conversation ID must be a valid UUID.",
                        color=0xFF0000,
                    ),
                    ephemeral=True,
                )

        answer: Answer = await self.async_ask_with_refresh(prompt, conversation_id=conversation_id)

        await ctx.interaction.followup.send(
            embed=(
                Embed(description=answer["message"])
                .set_footer(text=f"Conversation ID: {answer['conversation_id']}")
            )
        )

    @slash_command(guild_ids=GUILD_IDS)
    async def join(self, ctx: ApplicationContext, initial_prompt: str) -> None:
        """
        Joins a voice channel and starts a conversation with ChatGPT.
        :param ctx:
        :param initial_prompt:
        :return:
        """

        await ctx.interaction.response.defer()

        await ctx.bot.wait_until_ready()  # we have a hook on ready, so we need to wait for this

        if ctx.guild.voice_client is not None:
            await ctx.interaction.followup.send(
                embed=Embed(
                    title="Already in a voice channel",
                    description="I'm already in a voice channel. Please disconnect me first.",
                    color=0xFF0000,
                ),
                ephemeral=True,
            )
            return

        author_voice_state: VoiceState | None = cast(VoiceState, ctx.author.voice)  # type: ignore

        if author_voice_state is None or author_voice_state.channel is None:
            await ctx.interaction.followup.send(
                embed=Embed(
                    title="Not in a voice channel",
                    description="You must be in a voice channel to use this command.",
                    color=0xFF0000,
                ),
                ephemeral=True,
            )
            return

        if author_voice_state.channel.permissions_for(ctx.guild.me).connect is False:
            await ctx.interaction.followup.send(
                embed=Embed(
                    title="Can't connect!",
                    description="I do not have permission to connect to your voice channel.",
                    color=0xFF0000,
                ),
                ephemeral=True,
            )
            return

        if author_voice_state.channel.permissions_for(ctx.guild.me).speak is False:
            await ctx.interaction.followup.send(
                embed=Embed(
                    title="Can't speak!",
                    description="I do not have permission to speak in your voice channel.",
                    color=0xFF0000,
                ),
                ephemeral=True,
            )
            return

        voice_client: CustomVoiceClient = await author_voice_state.channel.connect(cls=CustomVoiceClient)

        initial_answer: Answer = await self.async_ask_with_refresh(initial_prompt,
                                                                   conversation_id=None)  # make new conversation
        conversation_id: str = initial_answer["conversation_id"]

        talk_callable: Callable[[str], None] = make_talk_callable(voice_client)
        speech_handler: Callable[[str], None] = self.make_speech_handler(voice_client, conversation_id)

        async_talk_callable: Callable[[str], Awaitable[None]] = make_async(talk_callable)
        async_speech_handler: Callable[[str], Awaitable[None]] = make_async(speech_handler)

        await async_talk_callable(initial_answer["message"])

        sink: Sink = AssemblyAITranscriptionSink(self.assembly_key, async_speech_handler)

        voice_client.start_recording(sink=sink, callback=lambda anonymous_sink, *args: None)

        await ctx.interaction.followup.send(
            embed=(
                Embed(
                    title="Connected!",
                    description=f"Connected to {author_voice_state.channel.mention}.\n"
                                f"Talk clearly at a normal pace and I'll respond to you.",
                    color=0x00FF00,
                )
                .set_footer(text=f"Conversation ID: {conversation_id}")
            )
        )


# this is not an extension, no setup function
__all__ = ("ChatGPTCog",)
