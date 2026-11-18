import logging
from pathlib import Path


def configure_logging() -> None:
    log_dir = Path.home() / "Library" / "Logs" / "Volca Sample 2 Studio"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "application.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
