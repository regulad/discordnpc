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

from asyncio import to_thread
from typing import Callable, Coroutine, ParamSpec, Any

A = ParamSpec("A")
R = ParamSpec("R")


def make_async(to_call: Callable[A, R]) -> Callable[A, Coroutine[Any, Any, R]]:
    """
    Make an async function from a callable.
    :param to_call: The callable to make the async function from.
    :return: A function that returns a coroutine to call the callable.
    """
    return lambda *args, **kwargs: to_thread(to_call, *args, **kwargs)  # type: ignore


__all__ = ("make_async",)
