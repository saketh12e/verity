"""
Citation Graph using NetworkX.
In-memory directed graph of source-to-source citation relationships found during crawling.
Detects circular citation loops and penalises looped sources.
Serializes to D3.js-compatible JSON for frontend force-directed graph rendering.
"""
import networkx as nx


class CitationGraph:
    """
    In-memory directed graph — one instance per research job in ResearchState.
    Nodes = source URLs. Edges = A cites B (directional).
    """

    def __init__(self):
        self.graph = nx.DiGraph()

    def add_source(self, url: str, domain: str, tier: str, title: str = "") -> None:
        """Register a source as a graph node."""
        self.graph.add_node(url, domain=domain, tier=tier, title=title)

    def add_citation(self, source_url: str, cites_url: str) -> None:
        """Source A cites Source B → directed edge A → B."""
        if source_url != cites_url:
            self.graph.add_edge(source_url, cites_url)

    def detect_circular_citations(self) -> list[tuple[str, str]]:
        """
        Find pairs (A, B) where A cites B AND B cites A.
        Returns list of sorted (url_a, url_b) tuples — no duplicates.
        """
        loops: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for node in self.graph.nodes():
            for neighbor in self.graph.successors(node):
                if self.graph.has_edge(neighbor, node):
                    pair = tuple(sorted([node, neighbor]))
                    if pair not in seen:
                        seen.add(pair)
                        loops.append(pair)
        return loops

    def get_loop_urls(self) -> set[str]:
        """Returns set of all URLs involved in citation loops."""
        urls: set[str] = set()
        for url_a, url_b in self.detect_circular_citations():
            urls.add(url_a)
            urls.add(url_b)
        return urls

    def is_in_loop(self, url: str) -> bool:
        return url in self.get_loop_urls()

    def confidence_penalty(self, url: str) -> float:
        """0.0 = clean source, 0.2 = in a citation loop."""
        return 0.2 if self.is_in_loop(url) else 0.0

    def to_json(self) -> dict:
        """
        Serialize to D3.js force-directed graph format.
        Frontend CitationGraph.jsx consumes this directly from GET /graph/{job_id}.
        """
        loop_urls = self.get_loop_urls()
        loops = self.detect_circular_citations()

        nodes = []
        for url, data in self.graph.nodes(data=True):
            nodes.append({
                "id": url,
                "domain": data.get("domain", url),
                "tier": data.get("tier", "opinion"),
                "title": data.get("title", ""),
                "in_loop": url in loop_urls,
                "citations_received": self.graph.in_degree(url),
            })

        edges = []
        for source, target in self.graph.edges():
            edges.append({
                "source": source,
                "target": target,
                "is_loop": self.graph.has_edge(target, source),
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "loops": [{"url_a": a, "url_b": b} for a, b in loops],
            "stats": {
                "total_sources": len(nodes),
                "total_citations": len(edges),
                "circular_loops": len(loops),
            },
        }
