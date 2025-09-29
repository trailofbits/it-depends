"""Functions for logging."""

import logging


def setup_logger(level: str) -> None:
    """Configure root logger for the application so all modules log to stdout."""
    level_name = level.upper()
    level_value = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level_value)
    # Remove all handlers associated with the root logger (avoid duplicate logs)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root_logger.addHandler(handler)
