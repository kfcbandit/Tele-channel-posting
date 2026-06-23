#!/usr/bin/env python3
"""
Helper to discover the chat ids needed for the two Telegram secrets.

Usage (locally):
    TELEGRAM_BOT_TOKEN=123456:abcdef python3 get_chat_id.py

Before running:
    1. In Telegram, open bot (search its @username), press START and send it
       any message, e.g. "hi".  -> this reveals PRIVATE chat id (review chat).
    2. Add the bot as an ADMINISTRATOR of your channel and post anything in the
       channel.                  -> this reveals your CHANNEL chat id.

The script prints every chat the bot has seen recently, with its id and type:
    type 'private'  -> use as TELEGRAM_REVIEW_CHAT_ID
    type 'channel'  -> use as TELEGRAM_CHANNEL_ID  (or just use @yourchannelusername)
"""
import os
import sys

import requests

token = os.environ.get("TELEGRAM_BOT_TOKEN")
if not token:
    sys.exit("Set TELEGRAM_BOT_TOKEN first, e.g.\n"
             "  TELEGRAM_BOT_TOKEN=123456:abcdef python3 get_chat_id.py")

resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
data = resp.json()
if not data.get("ok"):
    sys.exit(f"Telegram error: {data}")

seen = {}
for update in data.get("result", []):
    for key in ("message", "edited_message", "channel_post",
                "my_chat_member", "chat_member"):
        obj = update.get(key)
        if obj and obj.get("chat"):
            chat = obj["chat"]
            seen[chat["id"]] = chat

if not seen:
    print("No chats found yet.\n"
          "- Message your bot in Telegram (press START, send 'hi').\n"
          "- For the channel: add the bot as an admin and post something.\n"
          "Then run this again. (getUpdates only shows recent activity.)")
    sys.exit(0)

print("Chats the bot can currently see:\n")
for cid, chat in seen.items():
    label = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
    print(f"  id={cid}    type={chat.get('type')}    name={label}")

print("\n-> Use the 'private' chat id as TELEGRAM_REVIEW_CHAT_ID")
print("-> Use the 'channel' chat id (or @username) as TELEGRAM_CHANNEL_ID")
