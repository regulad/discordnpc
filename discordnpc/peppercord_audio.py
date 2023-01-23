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

import shlex
import subprocess
from abc import ABC
from asyncio import Queue, Future, Task, wait_for
from collections import deque
from io import BytesIO
from typing import Optional, cast

from discord import VoiceClient, Client, AudioSource, TextChannel, Thread, ClientException, PCMVolumeTransformer
from discord import abc
from discord.opus import Encoder


# These features are ported from another project of mine, regulad/PepperCord, which uses a custom Voice Client.
# It is modified here to work with py-cord.


class EnhancedSource(AudioSource, ABC):
    @property
    def duration(self) -> Optional[int]:
        """Get the length of the source. If this is not feasible, you can return None."""
        return None

    @property
    def name(self) -> str:
        return "Track"

    @property
    def description(self) -> str:
        return f'"{self.name}"'

    async def refresh(self, voice_client: CustomVoiceClient) -> EnhancedSource:
        """
        This allows you to fetch the source again,
        in case it is something like a YouTube video where the ability to read it decays after a set amount of time.
        """
        return self


class AudioQueue(Queue[EnhancedSource]):
    def _init(self, maxsize: int) -> None:
        self._queue: deque[EnhancedSource] = (
            deque(maxlen=maxsize) if maxsize > 0 else deque()
        )

    def _get(self) -> EnhancedSource:
        return self._queue.popleft()

    def _put(self, item: EnhancedSource) -> None:
        self._queue.append(item)

    @property
    def deque(self) -> deque:
        return self._queue


def _maybe_exception(future: Future[None], exception: Optional[Exception]) -> None:
    if exception is not None:
        future.set_exception(exception)
    else:
        future.set_result(None)


class CustomVoiceClient(VoiceClient):
    @staticmethod
    async def create(connectable: abc.Connectable, **kwargs) -> CustomVoiceClient:
        return await connectable.connect(**kwargs, cls=CustomVoiceClient)

    def __init__(self, client: Client, channel: abc.Connectable, maxsize: int = 0) -> None:
        super().__init__(client, channel)
        self.should_loop: bool = False
        self._task: Task = self.loop.create_task(self._run())
        self._audio_queue: AudioQueue = AudioQueue(maxsize=maxsize)
        self._bound_to: Optional[TextChannel | Thread] = None

        self._custom_state: dict = {}  # I just love these things!

        self.wait_for: Optional[int] = None

    def __getitem__(self, item):
        return self._custom_state[item]

    def __setitem__(self, key, value):
        self._custom_state[key] = value

    @property
    def queue(self) -> AudioQueue:
        return self._audio_queue

    @property
    def bound(self) -> Optional[TextChannel | Thread]:
        return self._bound_to

    def bind(self, to: TextChannel | Thread) -> None:
        assert self._bound_to is None
        assert isinstance(to, (TextChannel, Thread))
        self._bound_to = to

    def play_future(self, source: AudioSource) -> Future[None]:
        future: Future[None] = self.loop.create_future()
        self.play(source, after=lambda exception: _maybe_exception(future, exception))
        return future

    async def _run(self) -> None:
        """
        Plays tracks from the queue while tracks remain on the queue.
        This should be run in an async task.
        If the timeout is reached, a TimeoutError will be thrown.
        """
        try:
            while True:
                track: EnhancedSource = await wait_for(
                    self._audio_queue.get(), self.wait_for
                )

                while True:
                    track: EnhancedSource = await track.refresh(self)
                    try:
                        await self.play_future(track)
                    except Exception:
                        pass  # We don't care. Go on to the next one!
                    if not self.should_loop:
                        break
        except TimeoutError:
            if self.bound is not None:
                await self.bound.send("Ran out of tracks to play. Leaving...")
            self.wait_for: int = 120  # Reset this
        except Exception:
            raise
        finally:
            if self.is_connected():
                await self.disconnect(force=False)

    async def disconnect(self, *, force: bool = False) -> None:
        await super().disconnect(force=force)
        if not self._task.done():
            self._task.cancel()

    @property
    def source(self) -> Optional[EnhancedSource]:
        return cast(EnhancedSource, super().source)

    @property
    def ms_read(self) -> Optional[int]:
        """Returns the amount of milliseconds that have been read from the audio source."""
        if self._player is None:
            return None
        else:
            return self._player.loops * 20

    @property
    def progress(self) -> Optional[float]:
        """Returns a float 0-1 representing the distance through the track."""
        if self.source is None:
            return None
        elif self.source.duration is None:
            return None
        else:
            return self.ms_read / self.source.duration


class FFmpegPCMAudioBytes(AudioSource):
    """A hacky workaround to playing PCM audio with bytes."""

    def __init__(
            self,
            source: bytes,
            *,
            executable="ffmpeg",
            pipe=True,
            stderr=None,
            before_options=None,
            options=None
    ):
        stdin = None if not pipe else source
        args = [executable]
        if isinstance(before_options, str):
            args.extend(shlex.split(before_options))
        args.append("-i")
        args.append("-" if pipe else source)
        args.extend(("-f", "s16le", "-ar", "48000", "-ac", "2", "-loglevel", "warning"))
        if isinstance(options, str):
            args.extend(shlex.split(options))
        args.append("pipe:1")
        self._process = None
        try:
            self._process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr
            )
            self._stdout = BytesIO(self._process.communicate(input=stdin)[0])
        except FileNotFoundError:
            raise ClientException(executable + " was not found.") from None
        except subprocess.SubprocessError as exc:
            raise ClientException(
                "Popen failed: {0.__class__.__name__}: {0}".format(exc)
            ) from exc

    def read(self):
        ret = self._stdout.read(Encoder.FRAME_SIZE)
        if len(ret) != Encoder.FRAME_SIZE:
            return b""
        return ret

    def cleanup(self):
        proc = self._process
        if proc is None:
            return
        proc.kill()
        if proc.poll() is None:
            proc.communicate()

        self._process = None


class EnhancedTransformerSource(PCMVolumeTransformer, EnhancedSource, ABC):
    def __init__(self, source: AudioSource, *, volume: float = 1.0):
        super().__init__(source, volume=volume)


class EnhancedFFmpegPCMAudioBytesTransformed(EnhancedTransformerSource):
    def __init__(self, source: FFmpegPCMAudioBytes, *, volume: float = 1.0):
        super().__init__(source, volume=volume)

    async def refresh(self, client: CustomVoiceClient) -> EnhancedSource:
        # This source does not need to refresh.
        return self

    @classmethod
    def from_bytes(cls, source: bytes, *, volume: float = 1.0, **kwargs) -> EnhancedSource:
        return cls(FFmpegPCMAudioBytes(source, **kwargs), volume=volume)


# welcome to coupling HELL
# (only two classes AudioQueue and EnhancedFFmpegPCMAudioBytesTransformed are actually useful)
__all__ = [
    "CustomVoiceClient", "EnhancedSource", "AudioQueue", "FFmpegPCMAudioBytes", "EnhancedTransformerSource",
    "EnhancedFFmpegPCMAudioBytesTransformed"
]
