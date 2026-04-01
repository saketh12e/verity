"""
LLM Factory — V2
Two tiers of Gemini models:
  reasoning = gemini-3.1-pro-preview   → Decomposer, Conflict Detector, Devil's Advocate, Synthesis
  fast      = gemini-3.1-flash-lite-preview → All 4 Crawler agents (runs in parallel via Send())
"""
import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

_reasoning_llm: ChatGoogleGenerativeAI | None = None
_fast_llm: ChatGoogleGenerativeAI | None = None


def get_reasoning_llm() -> ChatGoogleGenerativeAI:
    """
    gemini-3.1-pro-preview — for Decomposer, Conflict Detector, Devil's Advocate, Synthesis.
    High reasoning quality, slower, more expensive.
    """
    global _reasoning_llm
    if _reasoning_llm is None:
        _reasoning_llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_REASONING_MODEL", "gemini-3.1-pro-preview"),
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0.1,
            max_output_tokens=16384,
        )
    return _reasoning_llm


def get_fast_llm() -> ChatGoogleGenerativeAI:
    """
    gemini-3.1-flash-lite-preview — for all 4 Crawler agents only.
    4× faster, 10× cheaper per token. Zero temperature for deterministic claim extraction.
    """
    global _fast_llm
    if _fast_llm is None:
        _fast_llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_FAST_MODEL", "gemini-3.1-flash-lite-preview"),
            google_api_key=os.getenv("GOOGLE_API_KEY"),
            temperature=0.0,
            max_output_tokens=4096,
        )
    return _fast_llm
