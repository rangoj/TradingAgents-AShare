from tradingagents.prompts import get_prompt


def test_zh_prompt_includes_role_knowledge_by_default():
    prompt = get_prompt("market_system_message", config={"prompt_language": "zh"})

    assert "【专业知识增强】" in prompt
    assert "先判断市场状态" in prompt


def test_zh_prompt_can_disable_role_knowledge():
    prompt = get_prompt(
        "market_system_message",
        config={"prompt_language": "zh", "enable_agent_knowledge_profiles": False},
    )

    assert "【专业知识增强】" not in prompt


def test_non_agent_prompt_does_not_get_knowledge_block():
    prompt = get_prompt("signal_extractor_system", config={"prompt_language": "zh"})

    assert "【专业知识增强】" not in prompt


def test_en_prompt_is_unchanged_by_knowledge_profiles():
    prompt = get_prompt("market_system_message", config={"prompt_language": "en"})

    assert "【专业知识增强】" not in prompt
    assert "Allowed indicators" in prompt
