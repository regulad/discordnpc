# DiscordNPC

[![Project Status: Concept – Minimal or no implementation has been done yet, or the repository is only intended to be a limited example, demo, or proof-of-concept.](https://www.repostatus.org/badges/latest/concept.svg)](https://www.repostatus.org/#concept)

[![wakatime](https://wakatime.com/badge/github/regulad/discordnpc.svg)](https://wakatime.com/badge/github/regulad/discordnpc)

DiscordNPC lets you interact with ChatGPT through a Discord voice channel, enabling a natural conversation.

Entirely stateless, and the bot will start a new conversation when it is removed from the call and placed back in.

It also includes a simple Discord command for asking questions.

Powered by [`google-speech`](https://pypi.org/project/google-speech/), [`acheong08/ChatGPT`](https://github.com/acheong08/ChatGPT),  and [`py-cord`](https://github.com/Pycord-Development/pycord).

## Video Demo

[YouTube Link](https://youtu.be/0Rs7h4ePgmw)

## Issues

There are many sources of latency, mainly the ChatGPT "API" and the AssemblyAI real-time transcription API. This leads to some long waiting times, but it works.

Additionally, ChatGPT has very low rate limits and will return a 429 error if you send too many requests. This is not handled by the bot, so you will have to wait for the rate limit to reset.

I implemented a rate limiter in the bot, but it is not very effective. It just says *"I lost my train of thought. Give me a minute to get back on track..."* right now.

## Installation

All manual baby! This project is a proof of content and does not currently include a Dockerfile.

Install with **`poetry install --no-root --without dev`** and you will be good to go.

Some project dependencies have native dependencies:

* [SoX](https://sox.sourceforge.net/)
  * Debian package: `sox`
  * MacOS brew: `brew install sox`
  * Windows download: https://sourceforge.net/projects/sox/files/sox/14.4.2/
* SoX MP3 Support
  * Debian package: `libsox-fmt-mp3`
  * MacOS brew: `brew install sox --with-lame`
  * It is hard to find Windows DLLs, so I included a `/bin` folder in the repository with the DLLs I found to work.
* `ffmpeg`

## Configuration

### Environment Variables

* `CHATGPT_*`: Configuration for [`acheong08/ChatGPT`](https://github.com/acheong08/ChatGPT). Arguments are analogous to the config.json format defined in [this wiki page for the ChatGPT python wrapper](https://github.com/acheong08/ChatGPT/wiki/Setup#config-options-optional).
  * i.e.: `CHATGPT_SESSION_TOKEN` is analogous to `session_token` in the config.json file.
* `DNPC_TOKEN`: Discord bot token. Can be created  in the [Discord Developer Portal](https://discord.com/developers/applications).
* `DNPC_WEBHOOK`: Discord webhook to log with. Designed for "production" use, not required.
* `DNPC_ASSEMBLY_TOKEN`: [AssemblyAI](https://www.assemblyai.com/) token for speech-to-text. Required for speech-to-text functionality. Can be obtained on the [app dashboard](https://www.assemblyai.com/app).
  * You'll need to have a paid account to use the real-time transcription. 
  * If you know an alternative to AssemblyAI that is free, tell me on Discord: `@regulad#7959`

## Execution

After setting the required environment variables, run **`poetry run python -m discordnpc`** to start the bot.

## Usage

DiscordNPC provides 2 Discord slash commands:

* `/ask`: Ask a simple question to ChatGPT.
* `/join`: Start a conversation in the voice channel you are currently connected to.
