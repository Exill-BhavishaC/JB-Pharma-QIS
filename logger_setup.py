"""
Module: logger_setup
Responsibility: Configures timestamped rotating file logger for use across modules.
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime


def get_logger(log_folder: str = ".", name: str = "qis_generator") -> logging.Logger:
    """
    Sets up and returns a timestamped rotating file logger.

    Uses Python's built-in per-logger handler guard (logger.handlers check)
    instead of a global flag, so re-entry from different call sites is safe.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.INFO)

        os.makedirs(log_folder, exist_ok=True)

        timestamp     = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_path = os.path.join(log_folder, f"qis_generation_{timestamp}.log")

        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )

        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(module)s] - %(message)s'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        logger.info(f"Logger successfully initialized at: {log_file_path}")

    return logger
