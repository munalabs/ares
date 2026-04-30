"""Combined entrypoint: starts the FastAPI server and NATS consumer concurrently."""

import asyncio
import logging
import os

import uvicorn

from ares_integration.engage import app
from ares_integration.nats_consumer import run_consumer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def _run() -> None:
    # Wire KB config into FastAPI app state so engage.py can access it
    app.state.argos_url = os.getenv("ARGOS_URL", "")
    app.state.argos_token = os.getenv("ARGOS_TOKEN", "")

    config = uvicorn.Config(app, host="0.0.0.0", port=8001, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(), run_consumer())


if __name__ == "__main__":
    asyncio.run(_run())
