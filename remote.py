#!/usr/bin/env python
"""Directly executable script for notifications and remote interaction via a Telegram bot."""

import argparse
import os
import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional

import requests
from requests.exceptions import ConnectionError as RequestConnectionError
from telethon import TelegramClient, events, types, errors
from monitor import Monitor, CallbacksAbstract
from utils import reboot_pi, get_ip
from confighandler import ConfigHandler


async def send(client, username: str | None, message: str, retry_count=0):
    """Send a Telegram message if a username/chat is configured."""
    if not username:
        return
    try:
        await client.send_message(username, message)
    except RequestConnectionError as error:
        if retry_count >= 5:
            print(f"Retried {retry_count} times, auto-rebooting now...")
            reboot_pi()
        backoff_time = 10
        print(
            f"ConnectionError on sending Telegram message, waiting {backoff_time} seconds to try again. Error: {error}"
        )
        await asyncio.sleep(backoff_time)
        await send(client, username, message, retry_count + 1)
    except ValueError as error:
        print(f"Skipping direct message; unable to resolve '{username}': {error}")


# setup callbacks
class Callbacks(CallbacksAbstract):
    def __init__(
        self,
        client: TelegramClient,
        username: str,
        topic_notifier: Optional[Callable[[str, str], Awaitable[bool]]],
        topic_key: Optional[str],
    ) -> None:
        self.client = client
        self.username = username
        self.meta = (client, username)
        self.topic_notifier = topic_notifier
        self.topic_key = topic_key
        super().__init__()

    async def _notify_topic(self, message: str) -> bool:
        if self.topic_notifier and self.topic_key:
            try:
                delivered = await self.topic_notifier(self.topic_key, message)
            except Exception as error:  # pragma: no cover - log and fall back
                print(f"Failed to notify topic: {error}")
                delivered = False
            if delivered:
                return True
        await send(*self.meta, message)
        return False

    async def on_start(self):
        message = f"New monitoring session!\nIP address: {get_ip()}"
        print(message)
        await self._notify_topic(message)
        await send(*self.meta, message)

    async def on_stop(self):
        await send(*self.meta, "This bot is done scouting the shelves, goodbye!")

    async def on_stock_available(self, message):
        await self._notify_topic(message)

    async def on_appointment_available(self, message):
        await send(*self.meta, message)

    async def on_newly_available(self):
        await self._notify_topic("✅ Stock just flipped to AVAILABLE!")

    async def on_auto_report(self, report: str):
        await self._notify_topic(report)
        await send(*self.meta, report)

    async def on_proxy_depletion(self, message: str):
        await send(*self.meta, message)

    async def on_long_processing_warning(self, warning: str):
        await send(*self.meta, warning)

    async def on_connection_error(self, error):
        await send(*self.meta, error)

    async def on_error(self, error: str, logfile_path: Path):
        await send(
            *self.meta,
            f"<b>Oops!</b> Something went wrong, the monitor <i>crashed</i>.\n  Reason: {error}",
        )
        await self.send_logfile(logfile_path)

    async def send_logfile(self, logfile_path):
        if logfile_path is not None:
            async with self.client.action(self.username, "document") as action:
                await self.client.send_file(
                    self.username,
                    logfile_path,
                    progress_callback=action.progress,
                    caption="Here's the log file!",
                )
        else:
            await send(
                *self.meta,
                f"Can't send the log file because there isn't one at {logfile_path}!",
            )


class TelegramConnection:
    """Class for sending notifications and receiving commands via a Telegram bot."""

    def __init__(self, configurationhandler: ConfigHandler) -> None:
        # first initialize the Telegram bot
        telegramconfig = configurationhandler.get(["telegram"])
        self.api_id = telegramconfig.get("api_id")
        self.api_hash = telegramconfig.get("api_hash")
        self.bot_token = telegramconfig.get("bot_token")
        self.session_name = telegramconfig.get("session_name")
        self.username = telegramconfig.get("username")
        self.group_id = telegramconfig.get("group_id") or None
        self.topic_key = telegramconfig.get("topic_key") or None
        raw_topics = telegramconfig.get("topics", {})
        self.topic_map = {}
        if isinstance(raw_topics, dict):
            for key, value in raw_topics.items():
                try:
                    self.topic_map[key] = int(value)
                except (TypeError, ValueError):
                    print(f"Ignoring invalid topic id for key '{key}': {value}")

        # creating a Telegram session and assigning it to a variable client
        client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        client.parse_mode = "html"
        client.start(bot_token=self.bot_token)

        self.client = client
        self.bot_api_base = f"https://api.telegram.org/bot{self.bot_token}"

        # registering the possible user commands
        commands_available = {
            "status": "retrieve the most recent check",
            "liststatus": "retrieve the statuses over the past report interval",
            "proxystatus": "retrieve the current proxy status",
            "plotprocessingtime": "plot the processing time over time",
            "plotavailability": "plot the availability over time",
            "getdata": "get the collected data as a CSV file",
            "getlog": "get the log file as a TXT file",
            "getconfig": "get the configuration file as a JSON file",
            "setconfig": "set the configuration file to the attachment (requires reboot)",
            "setpollinginterval": "set the polling interval in seconds (requires reboot)",
            "setreportinterval": "set the report interval (requires reboot)",
            "reboot": "reboot the Pi",
            "terminate": "terminate the monitor (it can no longer be accessed via Telegram!)",
        }
        commands_available_txt = "Commands available (use /setcommands in the Botfather chat to set these): \n"
        for command, description in commands_available.items():
            commands_available_txt += f"{command} - {description}\n"
        print(commands_available_txt)

        # set up the monitor
        callbacks = Callbacks(
            client,
            self.username,
            self.send_to_topic,
            self.topic_key,
        )
        self.monitor = Monitor(
            callbacks, path_to_config_file=str(configurationhandler.configfile_path)
        )

        # registering Telegram responses to the requests ((?i) makes it case insensitive)
        # status handler
        @client.on(events.NewMessage(pattern="(?i)/status"))
        async def handle_get_status(event):
            status = self.monitor.store_checker.get_last_status()
            await event.respond(status)

        # liststatus handler
        @client.on(events.NewMessage(pattern="(?i)/liststatus"))
        async def handle_list_status(event):
            statuslist = self.monitor.store_checker.get_statuslist()
            await event.respond(f"Overview of all recent statuses: \n{statuslist}")

        # proxystatus handler
        @client.on(events.NewMessage(pattern="(?i)/proxystatus"))
        async def handle_proxy_status(event):
            await event.respond(self.monitor.get_proxystatus())

        # termination handler
        @client.on(events.NewMessage(pattern="(?i)/terminate"))
        async def handle_terminate(event):
            self.monitor.save_df()
            await event.respond(
                "Terminating the monitor... \nTo start the monitor again, reboot."
            )
            exit(0)

        # reboot handler
        @client.on(events.NewMessage(pattern="(?i)/reboot"))
        async def handle_reboot(event):
            self.monitor.save_df()
            await event.respond("Rebooting, I'll be back...")
            reboot_pi()

        # getdata handler
        @client.on(events.NewMessage(pattern="(?i)/getdata"))
        async def handle_get_data(event):
            self.monitor.save_df()
            async with client.action(self.username, "document") as action:
                await client.send_file(
                    self.username,
                    "data.csv",
                    progress_callback=action.progress,
                    caption="Here's the data file!",
                )

        # getlog handler
        @client.on(events.NewMessage(pattern="(?i)/getlog"))
        async def handle_get_log(event):
            self.monitor.save_df()
            callbacks.send_logfile(self.monitor.get_logfile_path())

        # plotprocessingtime handler
        @client.on(events.NewMessage(pattern="(?i)/plotprocessingtime"))
        async def handle_plot_processing_time(event):
            filepath = self.monitor.plot_over_time(
                yaxis="processing_time", ylabel="Processing time in seconds"
            )
            async with client.action(self.username, "photo") as action:
                await client.send_file(
                    self.username,
                    filepath,
                    progress_callback=action.progress,
                    caption="Here's the plot!",
                )

        # plotavailability handler
        @client.on(events.NewMessage(pattern="(?i)/plotavailability"))
        async def handle_plot_availability(event):
            filepath = self.monitor.plot_over_time(
                yaxis="availability", ylabel="Available"
            )
            async with client.action(self.username, "photo") as action:
                await client.send_file(
                    self.username,
                    filepath,
                    progress_callback=action.progress,
                    caption="Here's the plot!",
                )

        # getconfig handler
        @client.on(events.NewMessage(pattern="(?i)/getconfig"))
        async def handle_get_config(event):
            async with client.action(self.username, "document") as action:
                await client.send_file(
                    self.username,
                    configurationhandler.configfile_path,
                    progress_callback=action.progress,
                    caption="Here's the configuration file!",
                )

        # setconfig handler
        @client.on(events.NewMessage(pattern="(?i)/setconfig"))
        async def handle_set_config(event):
            await event.respond(
                f"Attach a new `{configurationhandler.configfile_path}` in your next message and it will be set! Don't forget to delete data.csv in case something relevant changed."
            )

        # general handler for all uploaded files
        @client.on(events.NewMessage())
        async def handle_file_upload(event):
            if event.document is not None:
                # handle new config.json upload
                if (
                    event.document.mime_type == "application/json"
                    and types.DocumentAttributeFilename(
                        configurationhandler.configfile_path
                    )
                    in event.document.attributes
                ):
                    # check if we are in the correct folder before changing anything
                    if os.path.exists("monitor.py"):
                        # first remove the old config.json
                        os.remove(configurationhandler.configfile_path)
                        # then download the new one
                        config = await event.download_media()
                        await event.respond(
                            f"Succesfully set the new {configurationhandler.configfile_path} ({str(config)}). Reboot to apply."
                        )
                    else:
                        await event.respond(
                            f"The current working directory is not the directory of this application. Aborting {configurationhandler.configfile_path} replacement."
                        )
                else:
                    await event.respond(
                        f"If you were trying to set a new {configurationhandler.configfile_path}, make sure the file is named exactly that."
                    )

        # ensure client is in class state (already assigned above)

    async def send_to_topic(self, topic_key: str, message: str) -> bool:
        """Send a message to the configured forum topic or fall back to DM."""

        if self.group_id and topic_key in self.topic_map:
            thread_id = self.topic_map[topic_key]
            if thread_id <= 0:
                print(
                    f"Configured topic id {thread_id} for '{topic_key}' is invalid; falling back to DM."
                )
            else:
                loop = asyncio.get_running_loop()

                def post_message() -> bool:
                    url = f"{self.bot_api_base}/sendMessage"
                    payload = {
                        "chat_id": self.group_id,
                        "message_thread_id": thread_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    }
                    try:
                        response = requests.post(url, json=payload, timeout=10)
                        if response.ok:
                            return True
                        print(
                            f"Failed to send to topic '{topic_key}': {response.status_code} {response.text}"
                        )
                    except Exception as error:  # pragma: no cover
                        print(f"Failed to send to topic '{topic_key}': {error}")
                    return False

                if await loop.run_in_executor(None, post_message):
                    return True

        if self.username:
            await send(self.client, self.username, message)
        return False
    def start(self):
        # start the monitoring
        with self.client as client:
            try:
                client.loop.run_until_complete(self.monitor.start_monitoring())
            except KeyboardInterrupt:
                client.loop.run_until_complete(self.monitor.stop_monitoring())
            except errors.rpcerrorlist.AuthKeyDuplicatedError as error:
                print("Duplicate keys, removing the session file and rebooting")
                print(error)
                # await send(
                #     client,
                #     f"Duplicate keys detected, removing the session files and rebooting. \n\nFull error: \n{error}",
                # )
                os.remove("bot.session")
                reboot_pi()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apple Store stock notifier bot")
    parser.add_argument(
        "--config",
        default="./config.toml",
        help="Path to the configuration TOML file",
    )
    args = parser.parse_args()

    remote = TelegramConnection(ConfigHandler(args.config))
    remote.start()


if __name__ == "__main__":
    main()
