from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.tools import (
    get_global_news,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)

# The tools this analyst is offered; its tool node is built from the same tuple.
TOOLS = (
    get_news,
    get_global_news,
    get_macro_indicators,
    get_prediction_markets,
)


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(start_date, end_date) for news about the {asset_label} under analysis, get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve'), and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Start with get_news for the specific company/ticker and the analysis-date window. Only then use get_global_news for macro context. Do not treat unrelated global articles as company news, or infer a company-specific catalyst from a generic market headline. If ticker-scoped news is unavailable or undated, explicitly say that the direct company-news evidence is insufficient and avoid a confident company-specific directional news conclusion based only on global articles. In your summary, distinguish 'Company-specific evidence' from 'Global/macro context' and label missing ticker coverage clearly."""
            + """ For each major factual event, cite the publisher, article title, original URL, and publication date *only when those fields appear in the retrieved tool output*. Use publication dates no later than the run's analysis date. If a source link or publication date is missing, mark that claim as unverified instead of inventing a citation. Do not use live prediction-market results as historical evidence. If a feed is unavailable, say so explicitly. Add a short 'Sources and verification gaps' section at the end of the report."""
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Report what your tools support; another agent decides the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in TOOLS]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        result, report = take_turn(prompt, llm, TOOLS, state["messages"])

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
