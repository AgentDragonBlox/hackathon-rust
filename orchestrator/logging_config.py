"""
Centralized logging setup. Every tick and every external call funnels
through this logger — during the hackathon this is your fastest way
to see what actually happened when something looks wrong on screen.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging() -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("orchestrator")

    if _CONFIGURED:
        return logger

    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    file_handler = logging.FileHandler("orchestrator.log")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _CONFIGURED = True
    return logger


log = setup_logging()
