import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.graph.intent_parser import build_horizon_context
from tradingagents.agents.utils.agent_states import current_tracker_var, extract_verdict


def create_macro_analyst(llm, data_collector=None):
    async def _safe(tool, payload):
        try:
            return await asyncio.to_thread(tool.invoke, payload)
        except Exception as exc:
            return f"调用失败：{exc}"

    async def macro_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        print(f"[Macro Analyst] START {ticker} {current_date}")
        horizon = "medium"  # 宏观面固定中长期视角
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        system_message = get_prompt("macro_system_message", config=config) or ""
        horizon_ctx = build_horizon_context(horizon, focus_areas, specific_questions, agent_type="macro")

        pool = data_collector.get(ticker, current_date) if data_collector else None

        if pool is not None:
            board_flow = pool.get("fund_flow_board", "无数据")
            recent_news = pool.get("news", "无数据")
            global_news = pool.get("global_news", "无数据")
            zt_pool = pool.get("zt_pool", "无数据")
            hot_stocks = pool.get("hot_stocks", "无数据")
        else:
            from datetime import datetime, timedelta
            from tradingagents.agents.utils.agent_utils import (
                get_board_fund_flow,
                get_global_news,
                get_hot_stocks_xq,
                get_news,
                get_zt_pool,
            )
            days = 7
            end_dt = datetime.strptime(current_date, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=days)
            
            # Parallelize fallback fetches
            results = await asyncio.gather(
                _safe(get_board_fund_flow, {}),
                _safe(get_news, {
                    "ticker": ticker, "start_date": start_dt.strftime("%Y-%m-%d"), "end_date": current_date,
                }),
                _safe(get_global_news, {
                    "curr_date": current_date, "look_back_days": days, "limit": 30,
                }),
                _safe(get_zt_pool, {"date": current_date}),
                _safe(get_hot_stocks_xq, {}),
            )
            board_flow, recent_news, global_news, zt_pool, hot_stocks = results

        messages = [
            SystemMessage(content=(
                system_message
                + "\n\n请严格基于提供的数据输出报告，全程使用中文。"
                + "如果缺少指数、美股、北向、两融或市场宽度数据，必须明确写入数据缺口，不能推断或编造。"
            )),
            HumanMessage(content=(
                horizon_ctx + "\n"
                f"请分析 {ticker} 在 {current_date} 的宏观与板块环境。\n\n"
                f"【今日行业板块资金流向】\n{board_flow}\n\n"
                f"【涨停情绪池】\n{zt_pool}\n\n"
                f"【雪球热门股票】\n{hot_stocks}\n\n"
                f"【全球/宏观新闻】\n{global_news}\n\n"
                f"【标的近期相关新闻】\n{recent_news}\n\n"
                "【当前已知数据边界】\n"
                "- 当前未提供 A 股主要指数走势、全市场成交额、涨跌家数、市场宽度、北向资金、两融余额。\n"
                "- 当前未提供美股三大指数、纳指、VIX、美债收益率、美元指数、离岸人民币、A50 期货。\n"
                "- 请只基于板块资金流、涨停情绪、热门股票、全球/宏观新闻和标的新闻做保守判断。"
            )),
        ]

        # ── 实现 Token 级流式输出 ──────────────────
        tracker = current_tracker_var.get()
        full_content = ""
        async for chunk in llm.astream(messages):
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content
            if tracker:
                tracker._emit_token("Macro Analyst", "macro_report", content)

        print(f"[Macro Analyst] DONE {ticker}, report length={len(full_content)}")
        verdict, confidence = extract_verdict(full_content)
        return {
            "macro_report": full_content,
            "analyst_traces": [{
                "agent": "macro_analyst",
                "horizon": horizon,
                "data_window": "板块数据",
                "key_finding": f"宏观板块分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
            }],
        }

    return macro_analyst_node
