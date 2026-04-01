import json
import logging
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage
from agents.prompts.decomposer import SYSTEM_PROMPT
from services.llm_factory import get_reasoning_llm
from graph.state import ResearchState

load_dotenv()
logger = logging.getLogger(__name__)


def _parse_json(content) -> dict:
    # Gemini may return a list of content parts — extract text
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            return json.loads(content[start:end + 1])
        raise


async def decomposer_node(state: ResearchState) -> dict:
    """Split the research query into 4 focused sub-questions."""
    query = state["query"]
    job_id = state["job_id"]
    logger.info("[%s] Decomposing: %s", job_id, query)

    response = await get_reasoning_llm().ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"Research query: {query}"),
    ])

    parsed = _parse_json(response.content)
    sub_questions = parsed["sub_questions"]

    reasoning = parsed.get("reasoning", "")
    query_type = parsed.get("query_type", "deep")
    lookback_hours = parsed.get("lookback_hours", 8760)
    # Validate lookback_hours — never accept 0 or negative values
    if not isinstance(lookback_hours, int) or lookback_hours <= 0:
        lookback_hours = 8760
    logger.info("[%s] Decomposer reasoning: %s", job_id, reasoning[:120])
    logger.info("[%s] Generated %d sub-questions (query_type=%s, lookback_hours=%d)",
                job_id, len(sub_questions), query_type, lookback_hours)

    return {
        "sub_questions": sub_questions,
        "query_type": query_type,
        "lookback_hours": lookback_hours,
        "status": "crawling",
        "agent_logs": [
            "Decomposed into {} sub-questions: {} | {} | type={}, window={}h".format(
                len(sub_questions),
                " | ".join(sq["angle"] for sq in sub_questions),
                reasoning[:100],
                query_type,
                lookback_hours,
            )
        ],
    }
