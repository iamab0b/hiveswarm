import logging

import uvicorn

from ..config import load


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load()
    uvicorn.run(
        "hiveswarm.daemon.app:app",
        host=cfg.get("daemon.host", "0.0.0.0"),
        port=int(cfg.get("daemon.port", 7778)),
        log_level=cfg.get("daemon.log_level", "info"),
        access_log=bool(cfg.get("daemon.access_log", False)),
    )


if __name__ == "__main__":
    main()
