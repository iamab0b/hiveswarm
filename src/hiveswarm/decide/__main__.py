import logging

import uvicorn

from ..config import load


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load()
    uvicorn.run(
        "hiveswarm.decide.app:app",
        host=cfg.get("decide.host", "127.0.0.1"),
        port=int(cfg.get("decide.port", 9000)),
        log_level="info",
    )


if __name__ == "__main__":
    main()
