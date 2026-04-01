import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from agents.contracts import ResearchReport, PublisherResult
from graph.state import ResearchState

load_dotenv()
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.getenv("REPORT_OUTPUT_DIR", "reports"))


async def publisher_node(state: ResearchState) -> dict:
    """Save the report as JSON for the frontend to render."""
    report: ResearchReport | None = state.get("report")
    query = state["query"]
    job_id = state["job_id"]

    if not report:
        return {
            "published": PublisherResult(doc_url="", doc_id="", success=False),
            "status": "done",
            "agent_logs": ["Publisher: no report to publish"],
        }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    short_id = job_id[:8]
    json_path = OUTPUT_DIR / f"verity_{short_id}.json"

    payload = {
        "job_id": job_id,
        "query": query,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": report,
    }

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    doc_url = f"/reports/{json_path.name}"

    logger.info("[%s] Publisher: saved → %s", job_id, json_path)
    return {
        "published": PublisherResult(doc_url=doc_url, doc_id=str(json_path), success=True),
        "status": "done",
        "agent_logs": [f"Publisher: report ready — {len(report.get('sections', []))} sections"],
    }
