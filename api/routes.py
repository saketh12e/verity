import asyncio
import json
import uuid
import logging
from typing import AsyncIterator
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from graph.graph import graph
from graph.state import ResearchState

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory job store (Redis in V2)
job_queues: dict[str, asyncio.Queue] = {}
job_results: dict[str, dict] = {}
job_errors: dict[str, str] = {}

# SSE agent display names
NODE_LABELS: dict[str, dict] = {
    "decomposer":       {"icon": "🧩", "label": "Decomposer",        "start": "Splitting your question into research angles..."},
    "crawler_agent":    {"icon": "🔍", "label": "Crawler",           "start": "Searching and extracting claims..."},
    "dedup_agent":      {"icon": "🗃️",  "label": "Dedup",            "start": "Removing duplicate findings..."},
    "conflict_agent":   {"icon": "⚖️",  "label": "Conflict Detector","start": "Cross-checking sources for disagreements..."},
    "devil_advocate":   {"icon": "😈", "label": "Devil's Advocate",  "start": "Challenging high-confidence findings..."},
    "synthesis_agent":  {"icon": "📝", "label": "Synthesis",         "start": "Writing your research report..."},
}
TRACKED_NODES = set(NODE_LABELS.keys())


# ─── Request / Response models ────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str


class ResearchResponse(BaseModel):
    job_id: str
    status: str


# ─── Background task ──────────────────────────────────────────────────────────

async def _run_research(job_id: str, query: str) -> None:
    queue = job_queues[job_id]
    initial_state: ResearchState = {
        "query": query,
        "job_id": job_id,
        "sub_questions": [],
        "sub_question": None,
        "devil_advocate_target": None,
        "devil_advocate_targets": [],
        "raw_findings": [],
        "deduped_findings": [],
        "conflict_reports": [],
        "report": None,
        "published": None,
        "citation_graph_json": None,
        "lookback_hours": 8760,
        "query_type": "deep",
        "devil_advocate_metadata": [],
        "status": "decomposing",
        "agent_logs": [],
    }

    try:
        async for event in graph.astream_events(initial_state, version="v2"):
            evt_type = event["event"]
            name = event.get("name", "")

            if evt_type == "on_chain_start" and name in TRACKED_NODES:
                meta = NODE_LABELS[name]
                await queue.put({
                    "agent": name,
                    "label": meta["label"],
                    "icon": meta["icon"],
                    "status": "running",
                    "message": meta["start"],
                })

            elif evt_type == "on_chain_end" and name in TRACKED_NODES:
                output = event.get("data", {}).get("output", {})
                logs: list[str] = output.get("agent_logs", [])
                message = logs[-1] if logs else f"{NODE_LABELS[name]['label']} completed"
                meta = NODE_LABELS[name]
                await queue.put({
                    "agent": name,
                    "label": meta["label"],
                    "icon": meta["icon"],
                    "status": "done",
                    "message": message,
                })

            elif evt_type == "on_chain_end" and name == "verity":
                # Final state from the compiled graph
                final = event.get("data", {}).get("output", {})
                if final:
                    job_results[job_id] = final
                    logger.info("[%s] Graph complete", job_id)

    except Exception as e:
        logger.exception("[%s] Graph error: %s", job_id, e)
        job_errors[job_id] = str(e)
        await queue.put({"agent": "error", "status": "error", "message": str(e)})
    finally:
        await queue.put(None)  # Done sentinel


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/research", response_model=ResearchResponse)
async def create_research(request: ResearchRequest) -> ResearchResponse:
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")

    job_id = str(uuid.uuid4())
    job_queues[job_id] = asyncio.Queue()

    # Fire and forget — runs concurrently in the event loop
    asyncio.create_task(_run_research(job_id, request.query.strip()))

    logger.info("Started job %s for query: %s", job_id, request.query[:80])
    return ResearchResponse(job_id=job_id, status="started")


async def _event_generator(job_id: str) -> AsyncIterator[str]:
    queue = job_queues.get(job_id)
    if queue is None:
        yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
        return

    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=30.0)
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'heartbeat': True})}\n\n"
            continue

        if event is None:
            yield f"data: {json.dumps({'status': 'done', 'job_id': job_id})}\n\n"
            break

        yield f"data: {json.dumps(event)}\n\n"


@router.get("/stream/{job_id}")
async def stream_job(job_id: str) -> StreamingResponse:
    if job_id not in job_queues:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        _event_generator(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/result/{job_id}")
async def get_result(job_id: str) -> dict:
    if job_id in job_errors:
        raise HTTPException(status_code=500, detail=job_errors[job_id])
    if job_id not in job_results:
        raise HTTPException(status_code=404, detail="Result not ready or job not found")

    state = job_results[job_id]
    report = state.get("report")
    citation_graph = None
    if report and isinstance(report, dict):
        citation_graph = report.get("citation_graph")

    # Aggregate devil_advocate_metadata from all per-claim entries
    da_meta_list: list[dict] = state.get("devil_advocate_metadata", [])
    devil_advocate_ran = any(m.get("devil_advocate_ran", False) for m in da_meta_list)
    claims_challenged = sum(m.get("claims_challenged", 0) for m in da_meta_list)
    claims_downgraded = sum(m.get("claims_downgraded", 0) for m in da_meta_list)

    # Inject devil_advocate fields into report dict (synthesis.py is locked — added here)
    if report and isinstance(report, dict):
        report = {
            **report,
            "devil_advocate_ran": devil_advocate_ran,
            "claims_challenged": claims_challenged,
            "claims_downgraded": claims_downgraded,
        }

    return {
        "job_id": job_id,
        "status": "done",
        "report": report,
        "published": state.get("published"),
        "conflict_reports": state.get("conflict_reports", []),
        "citation_graph": citation_graph,
        "query": state.get("query", ""),
    }


@router.get("/export/{job_id}/pdf")
async def export_pdf(job_id: str) -> Response:
    """Export the research report as a PDF file."""
    if job_id in job_errors:
        raise HTTPException(status_code=500, detail=job_errors[job_id])
    if job_id not in job_results:
        raise HTTPException(status_code=404, detail="Result not ready or job not found")

    state = job_results[job_id]
    report = state.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="No report generated for this job")

    from services.pdf_exporter import export_to_pdf
    query = state.get("query", "")
    pdf_bytes = await export_to_pdf(report, query)

    safe_title = (report.get("title") or "verity-report")[:50].replace(" ", "_").replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
    )


@router.get("/graph/{job_id}")
async def get_citation_graph(job_id: str) -> dict:
    """Returns citation graph JSON for D3.js frontend rendering."""
    if job_id in job_errors:
        raise HTTPException(status_code=500, detail=job_errors[job_id])
    state = job_results.get(job_id, {})
    graph_json = state.get("citation_graph_json")
    if not graph_json:
        return {"nodes": [], "edges": [], "loops": [], "stats": {}}
    return graph_json


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "verity"}
