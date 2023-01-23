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

from logging import basicConfig, getLogger, Logger, StreamHandler, ERROR, INFO
from os import environ
from typing import Callable, Awaitable

from discord import Bot
from dislog import DiscordWebhookHandler
from revChatGPT.ChatGPT import Chatbot

from . import *

logger: Logger = getLogger(__name__)

root: Logger = Logger.root


def main() -> None:
    # Setup logging

    standard_handler: StreamHandler = StreamHandler()
    error_handler: StreamHandler = StreamHandler()

    standard_handler.addFilter(lambda record: record.levelno < ERROR)  # keep errors to stderr
    error_handler.setLevel(ERROR)

    basicConfig(
        format="%(asctime)s\t%(levelname)s\t%(name)s@%(threadName)s: %(message)s",
        level=INFO,  # discord debug is just a lot of information we don't really need
        handlers=[standard_handler, error_handler],
        force=True,  # doesn't work without this fsr
    )

    dislog_url: str = environ.get("DNPC_WEBHOOK", "")

    if not not dislog_url:  # i love javascript!!
        logger.info("Discord Webhook provided, enabling Discord logging.")

        handler: "DiscordWebhookHandler" = DiscordWebhookHandler(
            dislog_url,
            level=INFO,  # debug is just too much for discord to handle
            run_async=True,
        )
        root.addHandler(handler)

    logger.info("Logging setup complete.")

    # Boilerplate logger stuff done, lets move onto ChatGPT handling.

    chatgpt_config: dict[str, str] = {
        key.removeprefix("CHATGPT_").lower(): value for (key, value) in environ.items() if key.startswith("CHATGPT_")
    }

    make_chatbot: Callable[[], Awaitable[Chatbot]] = make_async(lambda: Chatbot(chatgpt_config))
    # runs some big io sync code in __init__, best to do on thread
    # this library is awful and each chatbot instance ALSO holds conversation data.

    # We now have the things we need to interact with ChatGPT, lets move onto Discord.

    bot: Bot = Bot()

    assembly_api_key: str = environ["DNPC_ASSEMBLY_TOKEN"]

    bot.add_cog(ChatGPTCog(bot, make_chatbot, assembly_api_key))
    # note: py-cord is different from discord.py in that it's cog loading functions are sync.
    # because of this, we can let it deal with loop management and just run the bot.

    # Let's get the show on the road!

    logger.info("Setup complete. Starting bot.")
    bot.run(environ["DNPC_TOKEN"])


if __name__ == "__main__":
    main()
