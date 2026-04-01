import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_decomposer_returns_four_sub_questions():
    mock_response = MagicMock()
    mock_response.content = """
    {
      "original_query": "Is coffee good for health?",
      "sub_questions": [
        {"id": 1, "question": "What does peer-reviewed research say about coffee and cardiovascular health?", "angle": "scientific", "source_preference": "arxiv"},
        {"id": 2, "question": "How do nutrition practitioners recommend coffee consumption?", "angle": "practitioner", "source_preference": "web"},
        {"id": 3, "question": "What are the documented risks and negative effects of daily coffee consumption?", "angle": "contrarian", "source_preference": "any"},
        {"id": 4, "question": "What has recent research (2022-2024) revealed about coffee and longevity?", "angle": "recent_news", "source_preference": "news"}
      ]
    }
    """

    with patch("agents.decomposer._get_llm") as mock_llm_factory:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.return_value = mock_llm

        from agents.decomposer import decomposer_node
        state = {"query": "Is coffee good for health?", "job_id": "test-job-1"}
        result = await decomposer_node(state)

    assert "sub_questions" in result
    assert len(result["sub_questions"]) == 4
    assert result["status"] == "crawling"
    assert len(result["agent_logs"]) == 1

    angles = {sq["angle"] for sq in result["sub_questions"]}
    assert "scientific" in angles
    assert "contrarian" in angles


@pytest.mark.asyncio
async def test_decomposer_handles_json_in_code_block():
    mock_response = MagicMock()
    mock_response.content = """Here is the decomposition:
```json
{
  "original_query": "test",
  "sub_questions": [
    {"id": 1, "question": "q1", "angle": "scientific", "source_preference": "arxiv"},
    {"id": 2, "question": "q2", "angle": "practitioner", "source_preference": "web"},
    {"id": 3, "question": "q3", "angle": "contrarian", "source_preference": "any"},
    {"id": 4, "question": "q4", "angle": "recent_news", "source_preference": "news"}
  ]
}
```"""

    with patch("agents.decomposer._get_llm") as mock_llm_factory:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.return_value = mock_llm

        from agents.decomposer import decomposer_node
        result = await decomposer_node({"query": "test", "job_id": "test-job-2"})

    assert len(result["sub_questions"]) == 4
