import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import uuid


def _make_deduped(claim: str, url: str, sq_id: int = 1, corroboration: int = 1):
    return {
        "id": str(uuid.uuid4()),
        "claim": claim,
        "primary_source_url": url,
        "corroboration_sources": [],
        "corroboration_count": corroboration,
        "confidence_score": 0.4,
        "source_tier": "secondary",
        "publication_date": "2024-01-01",
        "sub_question_id": sq_id,
    }


@pytest.mark.asyncio
async def test_conflict_detects_contradiction():
    findings = [
        _make_deduped("Remote work increases productivity by 20%",   "https://source1.com"),
        _make_deduped("Remote work decreases productivity significantly", "https://source2.com"),
    ]

    mock_response = MagicMock()
    mock_response.content = """{
      "conflicts": [
        {
          "claim_a": "Remote work increases productivity by 20%",
          "source_a": "https://source1.com",
          "claim_b": "Remote work decreases productivity significantly",
          "source_b": "https://source2.com",
          "conflict_type": "direct_contradiction",
          "explanation": "One source reports a productivity increase while the other reports a decrease."
        }
      ]
    }"""

    with patch("agents.conflict._get_llm") as mock_llm_factory:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.return_value = mock_llm

        from agents.conflict import conflict_node
        state = {"deduped_findings": findings, "job_id": "test-job"}
        result = await conflict_node(state)

    assert "conflict_reports" in result
    assert len(result["conflict_reports"]) == 2

    # At least one should be CONTESTED
    verdicts = {r["verdict"] for r in result["conflict_reports"]}
    assert "CONTESTED" in verdicts


@pytest.mark.asyncio
async def test_conflict_verified_when_no_conflicts():
    findings = [
        _make_deduped("Coffee reduces heart disease risk", "https://s1.com", corroboration=3),
        _make_deduped("Coffee is associated with lower mortality", "https://s2.com", corroboration=4),
    ]

    mock_response = MagicMock()
    mock_response.content = '{"conflicts": []}'

    with patch("agents.conflict._get_llm") as mock_llm_factory:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm_factory.return_value = mock_llm

        from agents.conflict import conflict_node
        state = {"deduped_findings": findings, "job_id": "test-job"}
        result = await conflict_node(state)

    verdicts = {r["verdict"] for r in result["conflict_reports"]}
    assert "CONTESTED" not in verdicts
    assert "VERIFIED" in verdicts  # corroboration >= 3


@pytest.mark.asyncio
async def test_conflict_empty_findings():
    with patch("agents.conflict._get_llm"):
        from agents.conflict import conflict_node
        result = await conflict_node({"deduped_findings": [], "job_id": "test"})

    assert result["conflict_reports"] == []
    assert result["status"] == "synthesizing"
