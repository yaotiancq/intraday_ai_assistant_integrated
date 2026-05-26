#!/usr/bin/env python3
"""
discord_watchlist_bot.py

Discord Slash Command Bot for controlling the running Futu watchlist monitor.

Commands:
/watch list
/watch period period:3m
/watch add symbol:NVDA
/watch add_many symbols:SPY QQQ NVDA AMD
/watch remove symbol:TSLA
/watch remove_many symbols:TSLA DIA XLE
/watch set symbols:SPY QQQ NVDA AMD
/watch clear
"""

from __future__ import annotations

import os
import re
from typing import List

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
ADMIN_API_URL = os.getenv("ADMIN_API_URL", "http://127.0.0.1:8765").rstrip("/")
WATCHLIST_ADMIN_TOKEN = os.getenv("WATCHLIST_ADMIN_TOKEN", "").strip()

_ALLOWED_RAW = os.getenv("ALLOWED_DISCORD_USER_IDS", "").strip()
ALLOWED_USER_IDS = {
    int(x.strip())
    for x in _ALLOWED_RAW.split(",")
    if x.strip().isdigit()
}


def normalize_symbol(raw: str) -> str:
    """
    Normalize user input.

    Examples:
    NVDA -> US.NVDA
    us.nvda -> US.NVDA
    US.NVDA -> US.NVDA
    """
    symbol = str(raw or "").strip().upper()

    if not symbol:
        raise ValueError("symbol is empty")

    if "." not in symbol:
        symbol = f"US.{symbol}"

    if not re.match(r"^US\.[A-Z0-9.\-]{1,20}$", symbol):
        raise ValueError(f"invalid US symbol: {symbol}")

    return symbol


def parse_symbols(raw: str) -> List[str]:
    """
    Parse multiple symbols from Discord input.

    Supported formats:
    SPY QQQ NVDA AMD
    SPY,QQQ,NVDA,AMD
    US.SPY,US.QQQ US.NVDA
    """
    parts = [p for p in re.split(r"[\s,]+", raw.strip()) if p]

    if not parts:
        raise ValueError("empty symbol list")

    # Deduplicate while preserving order
    seen = set()
    result = []

    for part in parts:
        code = normalize_symbol(part)
        if code not in seen:
            seen.add(code)
            result.append(code)

    return result


def format_symbols(symbols: List[str]) -> str:
    if not symbols:
        return "(empty)"

    text = ", ".join(symbols)

    # Avoid Discord message length issue.
    if len(text) > 1500:
        return text[:1500] + " ..."

    return text


def format_strategy_status(data: dict) -> str:
    bar_period = data.get("bar_period", "unknown")
    breakout_lookback = data.get("breakout_lookback", "unknown")
    breakout_minutes = data.get("breakout_lookback_minutes", "unknown")
    return (
        f"Period: `{bar_period}` | "
        f"Breakout lookback: `{breakout_lookback}` bars / `{breakout_minutes}` min"
    )


def is_allowed(interaction: discord.Interaction) -> bool:
    if not ALLOWED_USER_IDS:
        return False

    return interaction.user.id in ALLOWED_USER_IDS


async def admin_get(path: str) -> dict:
    headers = {"X-Admin-Token": WATCHLIST_ADMIN_TOKEN}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{ADMIN_API_URL}{path}", timeout=10) as resp:
            data = await resp.json()

            if resp.status >= 400:
                raise RuntimeError(data.get("error", str(data)))

            return data


async def admin_post(path: str, payload: dict) -> dict:
    headers = {"X-Admin-Token": WATCHLIST_ADMIN_TOKEN}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.post(
            f"{ADMIN_API_URL}{path}",
            json=payload,
            timeout=20,
        ) as resp:
            data = await resp.json()

            if resp.status >= 400:
                raise RuntimeError(data.get("error", str(data)))

            return data


async def reject_if_not_allowed(interaction: discord.Interaction) -> bool:
    if is_allowed(interaction):
        return False

    await interaction.response.send_message(
        "Unauthorized. Add your Discord user ID to ALLOWED_DISCORD_USER_IDS.",
        ephemeral=True,
    )
    return True


async def batch_add_symbols(codes: List[str]) -> dict:
    """
    Add multiple symbols by repeatedly calling the existing monitor endpoint:
    POST /watchlist/add

    This avoids requiring any change to the monitor API.
    """
    added = []
    noops = []
    failed = []
    last_symbols = []

    for code in codes:
        try:
            result = await admin_post("/watchlist/add", {"symbol": code})
            last_symbols = result.get("symbols", [])

            action = result.get("action")
            if action == "noop":
                noops.append(code)
            else:
                added.append(code)

        except Exception as exc:
            failed.append({"symbol": code, "error": str(exc)})

    return {
        "added": added,
        "noops": noops,
        "failed": failed,
        "symbols": last_symbols,
    }


async def batch_remove_symbols(codes: List[str]) -> dict:
    """
    Remove multiple symbols by repeatedly calling the existing monitor endpoint:
    POST /watchlist/remove

    This avoids requiring any change to the monitor API.
    """
    removed = []
    missing = []
    failed = []
    last_symbols = []

    for code in codes:
        try:
            result = await admin_post("/watchlist/remove", {"symbol": code})
            last_symbols = result.get("symbols", [])

            existed = result.get("existed")
            if existed:
                removed.append(code)
            else:
                missing.append(code)

        except Exception as exc:
            failed.append({"symbol": code, "error": str(exc)})

    return {
        "removed": removed,
        "missing": missing,
        "failed": failed,
        "symbols": last_symbols,
    }


watch_group = app_commands.Group(
    name="watch",
    description="Manage Futu monitor watchlist",
)


@watch_group.command(name="list", description="Show current monitored symbols")
async def watch_list(interaction: discord.Interaction):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        data = await admin_get("/watchlist")
        symbols = data.get("symbols", [])
        await interaction.followup.send(
            f"{format_strategy_status(data)}\nCurrent watchlist: `{format_symbols(symbols)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="period", description="Switch monitor bar period")
@app_commands.describe(period="Choose 1m, 3m, or 5m")
@app_commands.choices(period=[
    app_commands.Choice(name="1 minute", value="1m"),
    app_commands.Choice(name="3 minutes", value="3m"),
    app_commands.Choice(name="5 minutes", value="5m"),
])
async def watch_period(interaction: discord.Interaction, period: app_commands.Choice[str]):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        data = await admin_post("/strategy/bar-period", {"bar_period": period.value})
        symbols = data.get("symbols", [])
        action = data.get("action", "set_bar_period")
        await interaction.followup.send(
            f"Action: `{action}`\n{format_strategy_status(data)}\nCurrent watchlist: `{format_symbols(symbols)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="status", description="Show monitor period and watchlist")
async def watch_status(interaction: discord.Interaction):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        data = await admin_get("/strategy")
        symbols = data.get("symbols", [])
        await interaction.followup.send(
            f"{format_strategy_status(data)}\nCurrent watchlist: `{format_symbols(symbols)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="add", description="Add one US symbol to monitor")
@app_commands.describe(symbol="Example: NVDA or US.NVDA")
async def watch_add(interaction: discord.Interaction, symbol: str):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        code = normalize_symbol(symbol)
        data = await admin_post("/watchlist/add", {"symbol": code})
        symbols = data.get("symbols", [])

        await interaction.followup.send(
            f"Added `{code}`.\nCurrent: `{format_symbols(symbols)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="add_many", description="Add multiple US symbols to monitor")
@app_commands.describe(symbols="Example: SPY QQQ NVDA AMD or US.SPY,US.QQQ")
async def watch_add_many(interaction: discord.Interaction, symbols: str):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        codes = parse_symbols(symbols)
        result = await batch_add_symbols(codes)

        added = result["added"]
        noops = result["noops"]
        failed = result["failed"]
        current = result["symbols"]

        lines = []

        if added:
            lines.append(f"Added: `{format_symbols(added)}`")

        if noops:
            lines.append(f"Already existed: `{format_symbols(noops)}`")

        if failed:
            failed_text = ", ".join(
                f"{item['symbol']}({item['error']})"
                for item in failed
            )
            lines.append(f"Failed: `{failed_text}`")

        lines.append(f"Current: `{format_symbols(current)}`")

        await interaction.followup.send(
            "\n".join(lines),
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="remove", description="Remove one US symbol from monitor")
@app_commands.describe(symbol="Example: TSLA or US.TSLA")
async def watch_remove(interaction: discord.Interaction, symbol: str):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        code = normalize_symbol(symbol)
        data = await admin_post("/watchlist/remove", {"symbol": code})
        symbols = data.get("symbols", [])

        await interaction.followup.send(
            f"Removed `{code}`.\nCurrent: `{format_symbols(symbols)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="remove_many", description="Remove multiple US symbols from monitor")
@app_commands.describe(symbols="Example: TSLA DIA XLE or US.TSLA,US.DIA")
async def watch_remove_many(interaction: discord.Interaction, symbols: str):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        codes = parse_symbols(symbols)
        result = await batch_remove_symbols(codes)

        removed = result["removed"]
        missing = result["missing"]
        failed = result["failed"]
        current = result["symbols"]

        lines = []

        if removed:
            lines.append(f"Removed: `{format_symbols(removed)}`")

        if missing:
            lines.append(f"Not found: `{format_symbols(missing)}`")

        if failed:
            failed_text = ", ".join(
                f"{item['symbol']}({item['error']})"
                for item in failed
            )
            lines.append(f"Failed: `{failed_text}`")

        lines.append(f"Current: `{format_symbols(current)}`")

        await interaction.followup.send(
            "\n".join(lines),
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="set", description="Replace the full watchlist")
@app_commands.describe(symbols="Example: SPY QQQ NVDA AMD or US.SPY,US.QQQ")
async def watch_set(interaction: discord.Interaction, symbols: str):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        codes = parse_symbols(symbols)
        data = await admin_post("/watchlist/set", {"symbols": codes})
        current = data.get("symbols", [])

        await interaction.followup.send(
            f"Watchlist set to: `{format_symbols(current)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


@watch_group.command(name="clear", description="Clear the full watchlist")
async def watch_clear(interaction: discord.Interaction):
    if await reject_if_not_allowed(interaction):
        return

    await interaction.response.defer(ephemeral=True)

    try:
        data = await admin_post("/watchlist/clear", {})
        current = data.get("symbols", [])

        await interaction.followup.send(
            f"Watchlist cleared. Current symbols: `{format_symbols(current)}`",
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(f"Error: `{exc}`", ephemeral=True)


class WatchBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.add_command(watch_group)

        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Synced slash commands to guild {DISCORD_GUILD_ID}")
        else:
            await self.tree.sync()
            print(
                "Synced global slash commands. "
                "Global commands may take longer to appear."
            )

    async def on_ready(self):
        print(f"Logged in as {self.user}.")


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("Missing DISCORD_BOT_TOKEN in environment or .env file.")

    if not ALLOWED_USER_IDS:
        raise SystemExit(
            "Missing ALLOWED_DISCORD_USER_IDS. "
            "Refusing to start without an allowlist."
        )

    if not WATCHLIST_ADMIN_TOKEN:
        raise SystemExit(
            "Missing WATCHLIST_ADMIN_TOKEN in environment or .env file."
        )

    WatchBot().run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
