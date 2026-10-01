"""Module 6 logging setup."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Send structured-enough logs to stdout.

    Single handler on stdout rather than per-module loggers: a ranking run is a
    short batch job, and the useful log is the sequence of decisions it made, not
    a per-module firehose.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))