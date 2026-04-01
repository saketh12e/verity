"""
Citation Graph Agent.
Pure Python — no LLM needed.
Runs as a lightweight post-processor on crawler output.
Extracts citation relationships from page outbound links and adds them to the NetworkX graph.
"""
import logging
from urllib.parse import urlparse
from agents.contracts import CrawlerFinding
from services.citation_graph import CitationGraph

logger = logging.getLogger(__name__)

# Only track citations TO these trusted domains — avoids bloating graph with ad/nav links
TRUSTED_DOMAINS = {
    "pubmed.ncbi.nlm.nih.gov", "arxiv.org", "nature.com",
    "thelancet.com", "nejm.org", "who.int", "cdc.gov",
    "reuters.com", "bbc.com", "ft.com", "apnews.com",
    "cochrane.org", "science.org", "cell.com", "bmj.com",
    "jamanetwork.com", "nih.gov", "springer.com", "wiley.com",
}


def run_citation_graph_agent(
    findings: list[CrawlerFinding],
    graph: CitationGraph,
    raw_links: dict[str, list[str]],  # {source_url: [linked_urls]}
) -> CitationGraph:
    """
    Takes crawler findings + raw outbound links from each page.
    Registers source nodes and adds citation edges to the graph.
    """
    for finding in findings:
        source_url = finding["source_url"]
        domain = finding["source_domain"]
        tier = finding["source_tier"]

        graph.add_source(url=source_url, domain=domain, tier=tier)

        for linked_url in raw_links.get(source_url, []):
            linked_domain = urlparse(linked_url).netloc.lstrip("www.")
            if any(td in linked_domain for td in TRUSTED_DOMAINS):
                graph.add_source(url=linked_url, domain=linked_domain, tier="primary")
                graph.add_citation(source_url=source_url, cites_url=linked_url)

    loops = graph.detect_circular_citations()
    if loops:
        logger.warning("Citation graph: %d circular citation loops detected", len(loops))

    return graph
