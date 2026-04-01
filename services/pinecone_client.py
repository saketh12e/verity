import os
from dotenv import load_dotenv
load_dotenv()
from pinecone import Pinecone, ServerlessSpec

load_dotenv()

_pc: Pinecone | None = None
_index = None

DIMENSION = int(os.getenv("PINECONE_DIMENSION", "3072"))  # 3072 for gemini-embedding-2-preview


def get_index():
    """Return the Pinecone index, initializing on first call."""
    global _pc, _index
    if _index is not None:
        return _index

    _pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index_name = os.environ.get("PINECONE_INDEX_NAME", "verity-findings")

    existing = [i.name for i in _pc.list_indexes()]
    if index_name not in existing:
        _pc.create_index(
            name=index_name,
            dimension=DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    _index = _pc.Index(index_name)
    return _index


def upsert_finding(
    finding_id: str,
    embedding: list[float],
    metadata: dict,
    namespace: str = "default",
) -> None:
    index = get_index()
    index.upsert(
        vectors=[{"id": finding_id, "values": embedding, "metadata": metadata}],
        namespace=namespace,
    )


def query_similar(
    embedding: list[float],
    top_k: int = 5,
    namespace: str = "default",
) -> list[dict]:
    index = get_index()
    result = index.query(
        vector=embedding, top_k=top_k, include_metadata=True, namespace=namespace
    )
    return result.get("matches", [])


def upsert_batch(vectors: list[dict], namespace: str = "default") -> None:
    """Upsert a batch of vectors in one call (batch_size=100)."""
    index = get_index()
    index.upsert(vectors=vectors, namespace=namespace, batch_size=100)


def delete_namespace(namespace: str) -> None:
    """Delete all vectors for a given job namespace."""
    index = get_index()
    index.delete(delete_all=True, namespace=namespace)
