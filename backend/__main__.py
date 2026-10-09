"""Run the server:  python -m backend"""

import logging

import uvicorn

from .app import build_app
from .config import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
    settings = load_settings()
    log = logging.getLogger("panchayat")
    if settings.mock:
        log.warning("PANCHAYAT_MOCK=1: answering with bundled sample data, no SerpApi calls.")
    elif not settings.serpapi_key:
        log.warning("SERPAPI_API_KEY is not set. Copy .env.example to .env and add your key.")
    log.info("LLM: %s %s", settings.llm_provider, settings.llm_model)
    log.info("Open http://%s:%s", settings.host, settings.port)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
