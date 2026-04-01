import pytest
from unittest.mock import AsyncMock, patch, MagicMock


MOCK_PAGES = [
    {
        "url": "https://arxiv.org/abs/2301.00001",
        "title": "Coffee and Cardiovascular Health",
        "markdown": "Studies show that moderate coffee consumption (3-4 cups/day) is associated with a 15% reduction in cardiovascular disease risk. " * 10,
        "domain": "arxiv.org",
        "published_date": "2023-01-15",
    },
    {
        "url": "https://reuters.com/health/coffee-study",
        "title": "Reuters: New coffee study",
        "markdown": "A landmark study of 500,000 participants found that coffee drinkers live 1.8 years longer on average. " * 10,
        "domain": "reuters.com",
        "published_date": "2023-06-01",
    },
]


@pytest.mark.asyncio
async def test_crawler_extracts_findings():
    mock_opt_response = MagicMock()
    mock_opt_response.content = "coffee cardiovascular health study 2023"

    mock_extract_response = MagicMock()
    mock_extract_response.content = """[
      {"claim": "Moderate coffee consumption reduces cardiovascular disease risk by 15%.", "raw_excerpt": "Studies show that moderate coffee consumption (3-4 cups/day) is associated with a 15% reduction in cardiovascular disease risk."},
      {"claim": "Coffee drinkers live 1.8 years longer on average.", "raw_excerpt": "A landmark study of 500,000 participants found that coffee drinkers live 1.8 years longer on average."}
    ]"""

    with patch("agents.crawler._get_llm") as mock_llm_factory, \
         patch("agents.crawler.search", return_value=MOCK_PAGES):

        call_count = 0
        async def side_effect(messages):
            nonlocal call_count
            call_count += 1
            return mock_opt_response if call_count == 1 else mock_extract_response

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=side_effect)
        mock_llm_factory.return_value = mock_llm

        from agents.crawler import crawler_node
        state = {
            "query": "Is coffee good for health?",
            "job_id": "test-job",
            "sub_question": {
                "id": 1,
                "question": "What does research say about coffee and cardiovascular health?",
                "angle": "scientific",
                "source_preference": "arxiv",
            },
        }
        result = await crawler_node(state)

    assert "raw_findings" in result
    assert len(result["raw_findings"]) > 0
    finding = result["raw_findings"][0]
    assert "claim" in finding
    assert "source_url" in finding
    assert "source_tier" in finding
    assert finding["sub_question_id"] == 1


def test_source_tier_classification():
    from agents.crawler import _classify_tier
    assert _classify_tier("arxiv.org") == "primary"
    assert _classify_tier("nih.gov") == "primary"
    assert _classify_tier("mit.edu") == "primary"
    assert _classify_tier("reuters.com") == "secondary"
    assert _classify_tier("medium.com") == "opinion"
    assert _classify_tier("reddit.com") == "opinion"
