import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.langsmith_setup import setup_langsmith

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_langsmith()
    logging.getLogger(__name__).info("Verity API started — LangSmith tracing active")
    yield
    logging.getLogger(__name__).info("Verity API shutting down")


app = FastAPI(
    title="Verity Research API",
    description="Multi-agent deep research with source conflict detection",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routes import router  # noqa: E402  (import after app creation to avoid circular)
app.include_router(router)

# Serve saved JSON reports as static files
reports_dir = Path(os.getenv("REPORT_OUTPUT_DIR", "reports"))
reports_dir.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")
