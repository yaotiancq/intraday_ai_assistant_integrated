# Intraday AI Assistant（日内投资助手）

这是一个**只读行情数据 + 新闻催化 + 技术指标 + 优先级评分 + LLM 盘前报告 + Discord 推送**项目骨架。

它不是自动下单系统，不包含任何交易、下单、撤单、持仓修改代码。核心目标是每天盘前生成：

- 整体市场环境观察
- 强弱板块
- 当天值得观察的股票
- A/B/C 优先级
- 关键价位、触发条件、失效条件、风险提示

## 1. 项目结构

```text
intraday_ai_assistant/
  app/
    config.py
    data_sources/
      futu_client.py
      news_rss_client.py
    indicators/
      technical_indicators.py
    scoring/
      market_regime.py
      priority.py
    pipeline/
      candidate_builder.py
      evidence_builder.py
    llm/
      openai_client.py
      prompts.py
    validators/
      evidence_validator.py
      output_validator.py
    delivery/
      discord.py
    utils/
      file_io.py
      time_utils.py
  scripts/
    run_premarket.py
    run_single_stock_analysis.py
  tests/
  data/
  .env
  .env.example
  requirements.txt
```

## 2. 安装

```bash
cd intraday_ai_assistant
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 3. 配置 `.env`

```bash
cp .env.example .env
```

然后填写：

```env
OPENAI_API_KEY=你的OpenAI_API_Key
DISCORD_PREMARKET_WEBHOOK_URL=已在 .env 中填入
FUTU_HOST=127.0.0.1
FUTU_PORT=11111
```

> 说明：本版已根据你上传的 `discord_bot.txt` 写入 4 个 Discord webhook，并映射到盘前、开盘确认、盘中、盘后四类任务。

## 4. 启动 Futu OpenD

运行前请确保：

1. 已启动 Futu OpenD / Moomoo OpenD。
2. 已登录。
3. 本机端口默认是 `127.0.0.1:11111`。
4. 账号具备所需美股行情权限。

## 5. 运行盘前报告

```bash
python scripts/run_premarket.py --dry-run
```

生成文件：

```text
data/market_regime.json
data/news_snapshot.json
data/candidate_symbols.json
data/evidence_pack_premarket.json
data/premarket_report.md
```

真实推送 Discord：

```bash
python scripts/run_premarket.py --send-discord
```

## 6. 单只股票分析

```bash
python scripts/run_single_stock_analysis.py --symbol SATS --dry-run
```

## 7. 单元测试

```bash
pytest -q
```

单元测试包含 `tests/test_scripts.py`，会验证 `scripts/run_premarket.py` 和 `scripts/run_single_stock_analysis.py` 的 dry-run 执行。

## 8. 设计原则

本项目采用：

```text
确定性数据管道 → evidence_pack 证据包 → LLM 结构化分析 → 输出校验 → Discord 推送
```

不要让 LLM 自己随意决定事实。事实、新闻、价位、技术指标优先由代码生成，LLM 只负责基于证据包做中文结构化表达。

## 9. 风险提示

- 本项目仅用于投资研究与交易观察辅助。
- 不构成投资建议。
- 不保证行情、新闻、模型输出完全准确。
- 任何交易决策应由你自行判断。
