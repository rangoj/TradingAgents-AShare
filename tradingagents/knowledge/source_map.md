# Agent Knowledge Source Map

This folder contains distilled trading knowledge for the agent roster. The goal is not to quote books, but to convert reusable ideas into checklists, decision rules, and failure guards that can be injected into prompts or used by a retrieval layer.

## Local Book Inventory

Available under `/book`:

- `AI量化之道 让量化交易插上翅膀 B5.pdf`
- `《打开量化投资的黑箱（原书第2版）》 [里什].epub`
- `《量化投资：策略与技术》 [丁鹏].epub`
- `半小时漫画股票实战法.epub`
- `海龟交易法则（珍藏版） (中信金融投资经典系列) (（美）费思 [（美）费思]) (Z-Library).epub`
- `短线分时图  T+0交易实战技法  每天都抓涨停板 -- 股海淘金客著.pdf`
- `统计套利.pdf`
- `量化交易入门.pdf`

## Extraction Status

- EPUB titles were inspectable through the archive XHTML structure.
- PDF text extraction tools are not installed in this environment, so PDF-derived rules in this first pass are high-level domain distillation by title and existing project needs, not verified page-level extraction.
- Existing project prompts were reviewed from `tradingagents/prompts/zh.py`; the knowledge cards below are meant to complement, not replace, those prompts.

## Source Themes

### Quant System Design

Primary local sources:

- `《打开量化投资的黑箱（原书第2版）》`
- `《量化投资：策略与技术》`
- `量化交易入门.pdf`
- `统计套利.pdf`
- `AI量化之道 让量化交易插上翅膀 B5.pdf`

Distilled themes:

- Separate alpha, risk, transaction cost, portfolio construction, and execution.
- Treat data quality as part of the model, not as an implementation detail.
- Do not trust a signal without a defined horizon, cost assumption, and invalidation rule.
- Prefer robust, explainable edges over overfit indicator combinations.

### Trend Following And Risk

Primary local source:

- `海龟交易法则（珍藏版）`

Distilled themes:

- Think in probabilities, not predictions.
- Position sizing and exits are as important as entries.
- Avoid result bias, recency bias, and discretionary override after a loss.
- A trade idea must state risk unit, stop, expected payoff, and conditions that invalidate the system edge.

### A-Share Short-Term Trading

Primary local sources:

- `短线分时图  T+0交易实战技法  每天都抓涨停板 -- 股海淘金客著.pdf`
- `半小时漫画股票实战法.epub`

Distilled themes:

- A-share short-term signals must account for price limits, liquidity, theme cycles, retail emotion, and execution constraints.
- Intraday strength without follow-through can be distribution rather than accumulation.
- Limit-up pools, hot stocks, and fund flow are context signals, not standalone buy signals.

### Volume Price Analysis

Primary current project source:

- `tradingagents/prompts/zh.py` already contains a detailed VPA/Wyckoff prompt.

Distilled themes:

- Volume confirms or contradicts price action.
- Wide spread with low volume and narrow spread with high volume are warning signals.
- Wait for follow-through confirmation; do not infer a regime shift from one candle.

## Recommended Next Extraction Pass

1. Install or provide a PDF text extraction path for page-level extraction.
2. Create separate industry playbooks for banks, brokers, liquor, pharma, semiconductors, photovoltaic, new energy vehicles, real estate, military, internet/platform, and commodities.
3. Convert agent knowledge cards into prompt fragments or a lightweight retrieval loader.
4. Add backtest/evaluation cases where each agent's rule succeeds or fails.
