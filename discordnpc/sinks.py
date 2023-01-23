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
from __future__ import annotations

import json
from asyncio import sleep, Task, run_coroutine_threadsafe
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor, Executor
from logging import Logger, getLogger
from typing import Awaitable, Callable, Any, Iterator

import websockets
from discord import VoiceClient
from discord.sinks import Filters, PCMSink

ASSEMBLYAI_ENDPOINT = "wss://api.assemblyai.com/v2/realtime/ws?sample_rate={sample_rate}"

ASSEMBLYAI_MINIMUM_LENGTH_MS = 100
ASSEMBLYAI_MAXIMUM_LENGTH_MS = 2000

# ==START HACKY CODE==
ASSEMBLYAI_USABLE_MINIMUM_LENGTH_MS = 1000
# AssemblyAI's minimum length is 100ms, but it's not very good at that length.
# This is just here because I don't want to delete the minimum length code.
# Plus, I could refactor the code to handle this better anyway.
# ===END HACKY CODE===

ASSEMBLYAI_SESSION_BEGINS_MESSAGE = "SessionBegins"
ASSEMBLYAI_SESSION_RESUMED_MESSAGE = "SessionResumed"
ASSEMBLYAI_SESSION_TERMINATED_MESSAGE = "SessionTerminated"
ASSEMBLYAI_PARTIAL_TRANSCRIPT_MESSAGE = "PartialTranscript"
ASSEMBLYAI_FINAL_TRANSCRIPT_MESSAGE = "FinalTranscript"

use_accurate = True  # change this to whichever transcript you want to use.

transcript_to_use = ASSEMBLYAI_FINAL_TRANSCRIPT_MESSAGE if use_accurate else ASSEMBLYAI_PARTIAL_TRANSCRIPT_MESSAGE

logger: Logger = getLogger(__name__)


def split_bytes_into_chunks(iterable: bytes, chunk_size: int) -> Iterator[bytes]:
    return [iterable[i: i + chunk_size] for i in range(0, len(iterable), chunk_size)]


def calculate_length_of_data_ms(bytes_per_second: int, number_of_bytes: int) -> int:
    bytes_per_millisecond = bytes_per_second / 1000
    # print(f"recv'd {number_of_bytes} bytes, {bytes_per_millisecond} bytes/ms")
    return int(number_of_bytes / bytes_per_millisecond)


class AssemblyAITranscriptionSink(PCMSink):
    def __init__(
            self,
            assembly_ai_key: str,
            handle_text: Callable[[str], Awaitable[None]],
            *,
            filters=None) -> None:
        super().__init__(filters=filters)

        self.assembly_ai_key = assembly_ai_key
        self.handle_text = handle_text

        self.vc: VoiceClient | None = None  # py-cord typed this wrong

        self.sample_rate: int = 64000  # discord default

        self.transcription_task: Task | None = None
        self.websocket: Any | None = None

        self.send_audio: Callable[[bytes], Awaitable[None]] | None = None

        self.last_data: bytes = b""

        self.data_processing_executor: Executor = ThreadPoolExecutor(max_workers=1)

    async def _initialize_and_receive_transcription(self):
        async for websocket in websockets.connect(
                ASSEMBLYAI_ENDPOINT.format(sample_rate=self.sample_rate),
                ping_interval=5,
                ping_timeout=5,
                extra_headers={"Authorization": self.assembly_ai_key},
        ):
            try:
                await sleep(0.5)  # wait for connection to be established
                first_message = await websocket.recv()  # first message from
                first_message_json = json.loads(first_message)

                if first_message_json["message_type"] != ASSEMBLYAI_SESSION_BEGINS_MESSAGE:
                    raise RuntimeError(f"Expected SessionBegins message, got {first_message_json['message_type']}")

                session_id: str = first_message_json["session_id"]

                async def send_audio(data: bytes) -> None:
                    """receives PCM audio data"""
                    if sum(data) == 0:
                        return  # do not send silence
                    data_b64_string: str = str(b64encode(data).decode("utf-8"))
                    await websocket.send(json.dumps({"audio_data": data_b64_string}))

                self.send_audio = send_audio

                while True:
                    try:
                        message: str = await websocket.recv()
                        message_json: dict = json.loads(message)

                        if "error" in message_json:
                            raise RuntimeError(f"Error from AssemblyAI: {message_json['error']}")
                        elif message_json["message_type"] == transcript_to_use:
                            text: str = message_json["text"]
                            if len(text) > 0:
                                logger.info(f"Received text from AssemblyAI: {text}")
                                await self.handle_text(text)
                    except Exception as e:
                        logger.exception(e)
                        continue
            except websockets.ConnectionClosed:
                continue

    def init(self, vc: VoiceClient) -> None:
        super().init(vc)

        self.sample_rate = vc.channel.bitrate  # may need special handling to reopen websocket

        self.transcription_task = vc.loop.create_task(self._initialize_and_receive_transcription())

    def cleanup(self):
        super().cleanup()

        self.transcription_task.cancel()

    def send_sync(self, data: bytes) -> None:
        # final sanity check before sending it
        data_length_ms: int = calculate_length_of_data_ms(self.sample_rate, len(data))
        assert data_length_ms < ASSEMBLYAI_MAXIMUM_LENGTH_MS, "data is too long"
        assert data_length_ms > ASSEMBLYAI_MINIMUM_LENGTH_MS, "data is too short"

        if self.send_audio is not None:
            send_audio_coro: Awaitable[None] = self.send_audio(data)
            run_coroutine_threadsafe(send_audio_coro, self.vc.loop)
        else:
            logger.warning("have valid audio, but send_audio is None, cannot send audio to AssemblyAI")

    def process_data(self, data: bytes, user: int) -> None:
        if user == self.vc.user.id:
            return  # we don't want to send our own audio

        data_length_ms: int = calculate_length_of_data_ms(self.sample_rate, len(data))

        # hackland incoming
        # since assembly.ai has a limit on the length of audio it can process, we accumulate audio until we have enough

        if data_length_ms < ASSEMBLYAI_USABLE_MINIMUM_LENGTH_MS:  # TODO
            data = self.last_data + data

            # let's just slap the last one on it yeah?

            new_data_length_ms: int = calculate_length_of_data_ms(self.sample_rate, len(data))

            if new_data_length_ms < ASSEMBLYAI_USABLE_MINIMUM_LENGTH_MS:  # TODO
                self.last_data = data  # literally "double it and give it to the next person"
                return  # we still don't have enough data
            elif new_data_length_ms > ASSEMBLYAI_MAXIMUM_LENGTH_MS:
                self.last_data = b""
                return  # couldn't fix it 🥲
            else:
                self.last_data = b""  # processed the data
        elif data_length_ms > ASSEMBLYAI_MAXIMUM_LENGTH_MS:
            self.last_data = b""  # we don't need to accumulate anything

            seconds_per_chunk: float = float(ASSEMBLYAI_MAXIMUM_LENGTH_MS) / 1000.0
            chunk_size: int = int(seconds_per_chunk * self.sample_rate)

            chunk_size -= 10
            # I fear that floating point errors will cause this to be off by a few bytes and cause an error

            # our frame size is 2 bytes since it is PCM mono 16-bit

            if chunk_size % 2 != 0:
                chunk_size -= 1  # make sure it is divisible by 2 to not cut along a chunk

            for chunk in split_bytes_into_chunks(data, chunk_size):
                self.send_sync(chunk)
        else:
            self.last_data = b""  # we have enough data

        self.send_sync(data)

    @Filters.container
    def write(self, data: bytes, user: int) -> None:
        super().write(data, user)

        self.data_processing_executor.submit(self.process_data, data, user)


__all__ = ["AssemblyAITranscriptionSink"]
