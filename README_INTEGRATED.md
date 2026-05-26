# Intraday AI Assistant + Realtime Futu Monitor + Discord Control

This integrated project keeps the original AI premarket assistant behavior and adds:

1. U.S. trading-day gate for daily premarket runs.
2. Automatic publishing of the AI assistant's A/B-tier recommended symbols to the realtime monitor.
3. Discord Slash Command control for the realtime monitor watchlist.
4. Regular-hours-only monitor by default.
5. Explicit test switches for night/off-hour testing.

## Runtime services

```text
Oracle Ubuntu host
  ├─ Futu OpenD on 127.0.0.1:11111
  └─ docker compose
      ├─ monitor              # realtime 1m signal monitor + local admin API
      ├─ discord-bot          # /watch add/remove/set/list from phone
      └─ premarket-scheduler  # runs AI premarket once per trading day
```

## Default production flow

```text
05:45 PT on U.S. trading day
  -> scripts/run_premarket.py --send-discord --send-to-monitor
  -> AI premarket report pushed once to Discord
  -> A/B-tier evidence-pack candidates added to monitor watchlist
  -> monitor continues regular-hours-only signal monitoring
  -> Discord phone commands can still modify watchlist anytime
```

## Setup

```bash
cp .env.example .env
nano .env
```

Generate admin token:

```bash
openssl rand -hex 32
```

Fill these required fields in `.env`:

```text
OPENAI_API_KEY=...
DISCORD_PREMARKET_WEBHOOK_URL=...
DISCORD_WEBHOOK_URL=...
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=...
ALLOWED_DISCORD_USER_IDS=...
WATCHLIST_ADMIN_TOKEN=...
```

For the premarket assistant, keep `FUTU_EXTENDED_TIME=true` if you want
premarket K-line context before regular trading hours. The realtime monitor has
separate `MONITOR_EXTENDED_TIME` and `MONITOR_FUTU_SESSION` settings and remains
regular-hours-only by default.

## Build and run

```bash
docker compose build
docker compose up -d
```

Logs:

```bash
docker compose logs -f monitor
docker compose logs -f discord-bot
docker compose logs -f premarket-scheduler
```

## Manual test now at night

Default monitor remains regular-hours-only. For a quick integration test at night, use these two choices.

### A. Test only AI -> monitor watchlist update

This is safest and does not enable night trading signals:

```bash
docker compose exec premarket-scheduler python scripts/run_premarket.py \
  --dry-run \
  --force-run \
  --allow-non-trading-day-test \
  --send-to-monitor
```

Then verify:

```bash
set -a
source .env
set +a
curl -H "X-Admin-Token: $WATCHLIST_ADMIN_TOKEN" http://127.0.0.1:8765/watchlist
```

### B. Temporarily test realtime monitor signal behavior at night

Edit `.env`:

```text
MONITOR_TEST_MODE=true
MONITOR_EXTENDED_TIME=true
MONITOR_FUTU_SESSION=ALL
```

Restart only monitor:

```bash
docker compose up -d --force-recreate monitor
```

After testing, restore:

```text
MONITOR_TEST_MODE=false
MONITOR_EXTENDED_TIME=false
MONITOR_FUTU_SESSION=RTH
```

Then restart monitor again.

## Discord mobile commands

```text
/watch list
/watch add symbol: NVDA
/watch remove symbol: TSLA
/watch set symbols: SPY QQQ NVDA AMD
/watch clear
```

## Important safety

- Do not commit `.env`.
- Keep admin API bound to `127.0.0.1`.
- Keep `WATCHLIST_ADMIN_TOKEN` set. The monitor refuses to start without it
  unless `MONITOR_ALLOW_EMPTY_ADMIN_TOKEN=true` is explicitly enabled for an
  isolated local test.
- This system is signal-only; it does not place orders.
- Rotate any webhook or bot token that has been pasted into chat or shared externally.
