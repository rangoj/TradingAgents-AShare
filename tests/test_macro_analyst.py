import asyncio

from tradingagents.agents.analysts.macro_analyst import create_macro_analyst


class _FakeChunk:
    content = '<!-- VERDICT: {"direction": "中性", "reason": "数据边界明确"} -->'


class _FakeLLM:
    def __init__(self):
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        yield _FakeChunk()


class _FakeCollector:
    def get(self, ticker, trade_date):
        return {
            "fund_flow_board": "板块资金流",
            "news": "标的新闻",
            "global_news": "全球宏观新闻",
            "zt_pool": "涨停情绪",
            "hot_stocks": "热门股票",
        }


def test_macro_analyst_uses_existing_market_context_data():
    llm = _FakeLLM()
    node = create_macro_analyst(llm, data_collector=_FakeCollector())

    state = {
        "trade_date": "2026-06-15",
        "company_of_interest": "600519",
        "user_intent": {"focus_areas": [], "specific_questions": []},
    }

    result = asyncio.run(node(state))
    human_message = llm.messages[1].content
    system_message = llm.messages[0].content

    assert "板块资金流" in human_message
    assert "涨停情绪" in human_message
    assert "热门股票" in human_message
    assert "全球宏观新闻" in human_message
    assert "标的新闻" in human_message
    assert "当前已知数据边界" in human_message
    assert "美股三大指数" in human_message
    assert "不能推断或编造" in system_message
    assert result["macro_report"]
