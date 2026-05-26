# Intraday AI Assistant（日内投资助手）

这是一个**只读行情数据 + 新闻催化 + 技术指标 + 优先级评分 + LLM 盘前报告 + Discord 推送 + Futu 实时监控**项目。

它不是自动下单系统，不包含下单、撤单、修改持仓等交易代码。核心目标是：

- 每个美股交易日盘前生成结构化观察报告
- 根据 evidence pack 给股票分 A/B/C/D 优先级
- 把 A/B 级股票同步到实时监控 watchlist
- 在实时监控中输出 WATCH/BUY/SELL 信号提示
- 通过 Discord webhook 和 slash command 在手机端查看、调整 watchlist

集成版额外提供：

- 美股交易日 gate，避免非交易日自动运行盘前任务
- 盘前助手自动发布 A/B 级候选到 realtime monitor
- Discord slash command 远程控制 monitor watchlist
- monitor 默认只在常规交易时段运行
- 夜间/非交易时段测试开关

## 1. 核心流程

```text
05:45 PT，美股交易日
  -> scripts/run_premarket.py --send-discord --send-to-monitor
  -> 生成盘前 evidence pack 和中文报告
  -> 报告推送到 Discord
  -> A/B 级候选股票同步到 realtime monitor
  -> monitor 在常规交易时段监听 1m K 线信号
  -> Discord /watch 命令可随时调整 watchlist
```

## 2. 项目结构

```text
app/
  config.py
  data_sources/          # Futu / RSS clients
  indicators/            # 技术指标与关键价位
  scoring/               # 市场环境与候选股评分
  pipeline/              # candidate/evidence pack 构建
  llm/                   # OpenAI prompt 与报告生成
  validators/            # evidence/report 校验
  delivery/              # Discord webhook
  integration/           # 交易日判断、monitor bridge
  utils/
scripts/
  run_premarket.py
  run_single_stock_analysis.py
  run_realtime_monitor.py
  run_daily_premarket_scheduler.py
  discord_watchlist_bot.py
tests/
data/
```

## 3. 本地安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. 配置 `.env`

```bash
cp .env.example .env
nano .env
```

至少填写：

```env
OPENAI_API_KEY=...
DISCORD_PREMARKET_WEBHOOK_URL=...
DISCORD_WEBHOOK_URL=...
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=...
ALLOWED_DISCORD_USER_IDS=...
WATCHLIST_ADMIN_TOKEN=...

FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_EXTENDED_TIME=true
```

生成 `WATCHLIST_ADMIN_TOKEN`：

```bash
openssl rand -hex 32
```

`FUTU_EXTENDED_TIME=true` 建议用于盘前助手，这样报告可以使用盘前 K 线环境。实时 monitor 另有 `MONITOR_EXTENDED_TIME` 和 `MONITOR_FUTU_SESSION`，默认保持常规交易时段：

```env
MONITOR_TEST_MODE=false
MONITOR_EXTENDED_TIME=false
MONITOR_FUTU_SESSION=RTH
MONITOR_BAR_PERIOD=1m
MONITOR_ALLOW_EMPTY_ADMIN_TOKEN=false
```

`WATCHLIST_ADMIN_TOKEN` 必须设置。monitor 现在会拒绝在无 token 状态启动，除非你显式设置 `MONITOR_ALLOW_EMPTY_ADMIN_TOKEN=true` 做隔离本地测试。

## 5. Futu / Moomoo OpenD

运行前请确认：

1. Futu OpenD / Moomoo OpenD 已启动并登录。
2. OpenD 地址与 `.env` 一致，默认 `127.0.0.1:11111`。
3. 账号具备所需美股行情权限。
4. Docker 部署时，本项目使用 `network_mode: host` 连接本机 OpenD。

## 6. 盘前报告

本地 dry-run：

```bash
python scripts/run_premarket.py --dry-run
```

非交易日或夜间测试：

```bash
python scripts/run_premarket.py \
  --dry-run \
  --force-run \
  --allow-non-trading-day-test
```

真实推送 Discord：

```bash
python scripts/run_premarket.py --send-discord
```

推送 Discord，并把 A/B 级候选同步到 monitor：

```bash
python scripts/run_premarket.py --send-discord --send-to-monitor
```

生成文件：

```text
data/market_regime.json
data/news_snapshot.json
data/candidate_symbols.json
data/evidence_pack_premarket.json
data/technical_levels.json
data/premarket_report.md
data/warnings.json
```

## 7. 单只股票分析

```bash
python scripts/run_single_stock_analysis.py --symbol NVDA --dry-run
```

输出：

```text
data/evidence_pack_NVDA.json
data/stock_report_NVDA.md
```

## 8. Docker 运行集成服务

典型部署结构：

```text
Oracle Ubuntu host
  ├─ Futu OpenD on 127.0.0.1:11111
  └─ docker compose
      ├─ monitor              # realtime signal monitor + local admin API
      ├─ discord-bot          # Discord /watch commands from phone
      └─ premarket-scheduler  # runs AI premarket once per trading day
```

```bash
docker compose build
docker compose up -d
```

服务：

```text
monitor              # 实时 1m/3m/5m signal monitor + local admin API
discord-bot          # Discord /watch add/remove/set/list/status/period
premarket-scheduler  # 每个美股交易日运行一次盘前助手
```

查看日志：

```bash
docker compose logs -f monitor
docker compose logs -f discord-bot
docker compose logs -f premarket-scheduler
```

重启单个服务：

```bash
docker compose up -d --force-recreate monitor
```

## 9. Monitor Watchlist

查看当前 watchlist：

```bash
curl -H "X-Admin-Token: $WATCHLIST_ADMIN_TOKEN" \
  http://127.0.0.1:8765/watchlist
```

手动添加 symbol：

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $WATCHLIST_ADMIN_TOKEN" \
  -d '{"symbol":"US.NVDA"}' \
  http://127.0.0.1:8765/watchlist/add
```

Discord 手机端命令：

```text
/watch list
/watch status
/watch period period: 1m
/watch period period: 3m
/watch period period: 5m
/watch add symbol: NVDA
/watch add_many symbols: SPY QQQ NVDA AMD
/watch remove symbol: TSLA
/watch set symbols: SPY QQQ NVDA AMD
/watch clear
```

Monitor 支持 `1m`、`3m`、`5m` 三种 K 线周期。切换周期时，monitor 会重新订阅对应 Futu K 线并重建当前 watchlist 的缓存 K 线。

当前硬编码 breakout lookback：

```text
1m -> 20 bars，约 20 分钟
3m -> 10 bars，约 30 分钟
5m ->  6 bars，约 30 分钟
```

## 10. 夜间/非交易时段测试

只测试 AI -> monitor watchlist 同步，不打开夜间实时信号：

```bash
docker compose exec premarket-scheduler python scripts/run_premarket.py \
  --dry-run \
  --force-run \
  --allow-non-trading-day-test \
  --send-to-monitor
```

验证 watchlist：

```bash
set -a
source .env
set +a
curl -H "X-Admin-Token: $WATCHLIST_ADMIN_TOKEN" \
  http://127.0.0.1:8765/watchlist
```

如果要临时测试 monitor 夜间实时信号，在 `.env` 中改：

```env
MONITOR_TEST_MODE=true
MONITOR_EXTENDED_TIME=true
MONITOR_FUTU_SESSION=ALL
```

然后重启 monitor：

```bash
docker compose up -d --force-recreate monitor
```

测试完改回：

```env
MONITOR_TEST_MODE=false
MONITOR_EXTENDED_TIME=false
MONITOR_FUTU_SESSION=RTH
```

## 11. 测试

```bash
.venv/bin/python -m pytest -q
```

当前测试覆盖：

- 技术指标
- 市场环境评分
- 候选股评分
- evidence/report 校验
- Futu mock client 与 session 配置
- RSS ticker 匹配与错误兜底
- premarket/single-stock dry-run 脚本
- monitor admin token 安全检查

## 12. 设计原则

本项目采用：

```text
确定性数据管道 -> evidence pack -> LLM 结构化分析 -> 输出校验 -> Discord/monitor
```

不要让 LLM 自己决定事实。行情、新闻、价位、技术指标优先由代码生成，LLM 只负责基于 evidence pack 做中文结构化表达。

## 13. 安全与风险

- 不要提交 `.env`。
- `WATCHLIST_ADMIN_TOKEN` 必须设置并妥善保存。
- Admin API 保持绑定 `127.0.0.1`。
- 如果 webhook、bot token、OpenAI key 曾被粘贴到聊天或公开位置，请立即轮换。
- 本项目仅用于投资研究与交易观察辅助。
- 不构成投资建议。
- 不保证行情、新闻、模型输出完全准确。
- 任何交易决策应由你自行判断。
