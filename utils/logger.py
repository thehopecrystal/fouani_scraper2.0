"""
logger.py
Sets up three log files (scraper.log, errors.log, downloads.log) plus
a Qt-signal-friendly handler so the GUI log window gets live updates.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:  # allow core/ modules to be imported without Qt for testing
    QObject = object
    Signal = None


class QtLogEmitter(QObject):
    if Signal is not None:
        new_record = Signal(str, str)  # level, message
    else:
        new_record = None


_emitter = QtLogEmitter() if Signal is not None else None


class QtLogHandler(logging.Handler):
    """Forwards log records to the Qt GUI via a signal."""

    def emit(self, record):
        if _emitter is None:
            return
        msg = self.format(record)
        try:
            _emitter.new_record.emit(record.levelname, msg)
        except RuntimeError:
            # Qt object may have been destroyed already (app shutting down)
            pass


def get_emitter():
    return _emitter


def setup_logging(logs_folder: str) -> logging.Logger:
    os.makedirs(logs_folder, exist_ok=True)

    logger = logging.getLogger("fouani_scraper")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S")

    # scraper.log - everything
    main_handler = RotatingFileHandler(
        os.path.join(logs_folder, "scraper.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    main_handler.setLevel(logging.DEBUG)
    main_handler.setFormatter(fmt)
    logger.addHandler(main_handler)

    # errors.log - errors only
    err_handler = RotatingFileHandler(
        os.path.join(logs_folder, "errors.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(fmt)
    logger.addHandler(err_handler)

    # downloads.log - image/download related records only
    class DownloadFilter(logging.Filter):
        def filter(self, record):
            return getattr(record, "category", "") == "download"

    dl_handler = RotatingFileHandler(
        os.path.join(logs_folder, "downloads.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    dl_handler.setLevel(logging.DEBUG)
    dl_handler.setFormatter(fmt)
    dl_handler.addFilter(DownloadFilter())
    logger.addHandler(dl_handler)

    # console
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # Qt GUI
    qt_handler = QtLogHandler()
    qt_handler.setLevel(logging.DEBUG)
    qt_handler.setFormatter(fmt)
    logger.addHandler(qt_handler)

    return logger


def log_download(logger, message, level=logging.INFO):
    logger.log(level, message, extra={"category": "download"})
