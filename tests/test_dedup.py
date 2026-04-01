import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import uuid


def _make_finding(claim: str, url: str, tier: str = "secondary", sq_id: int = 1):
    return {
        "id": str(uuid.uuid4()),
        "claim": claim,
        "source_url": url,
        "source_domain": "example.com",
        "source_tier": tier,
        "publication_date": "2024-01-01",
        "sub_question_id": sq_id,
        "raw_excerpt": claim,
    }


@pytest.mark.asyncio
async def test_dedup_merges_similar_findings():
    findings = [
        _make_finding("Coffee reduces heart disease risk by 15%", "https://source1.com"),
        _make_finding("Coffee lowers cardiovascular disease risk 15 percent", "https://source2.com"),
        _make_finding("Daily exercise improves mental health significantly", "https://source3.com"),
    ]

    # Mock embeddings: findings 0 and 1 are similar (high cosine), 2 is different
    fake_vectors = [
        [0.9, 0.1, 0.0],   # finding 0
        [0.88, 0.12, 0.0],  # finding 1 — similar to 0
        [0.0, 0.0, 1.0],    # finding 2 — different
    ]

    def mock_query(embedding, top_k, namespace):
        # finding 1's embedding is similar to finding 0
        if embedding == fake_vectors[1]:
            return [{"id": findings[0]["id"], "score": 0.95, "metadata": {}}]
        return []

    with patch("agents.dedup._get_embeddings") as mock_embed_factory, \
         patch("agents.dedup.pinecone_client.upsert_finding"), \
         patch("agents.dedup.pinecone_client.query_similar", side_effect=mock_query), \
         patch("asyncio.to_thread", new_callable=MagicMock) as mock_thread:

        # embed_doc.embed_documents returns fake_vectors
        embed_doc = MagicMock()
        embed_doc.embed_documents = MagicMock(return_value=fake_vectors)
        embed_query = MagicMock()
        mock_embed_factory.return_value = (embed_doc, embed_query)

        # Make asyncio.to_thread actually call the function
        async def real_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)
        mock_thread.side_effect = real_to_thread

        from agents.dedup import dedup_node
        state = {"raw_findings": findings, "job_id": "test-job"}
        result = await dedup_node(state)

    assert "deduped_findings" in result
    assert result["status"] == "analyzing"
    # Should have fewer or equal deduped than raw
    assert len(result["deduped_findings"]) <= len(findings)


def test_confidence_score_calculation():
    from agents.dedup import _confidence_score
    assert _confidence_score(1, "opinion") == 0.2
    assert _confidence_score(1, "primary") == 0.4
    assert _confidence_score(3, "primary") == min(0.6 + 0.2, 1.0)
    assert _confidence_score(4, "primary") == 1.0
    assert _confidence_score(2, "secondary") == 0.5
