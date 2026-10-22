import json
import logging
from datetime import UTC, datetime

from Slop.Output.Style import Style


class JsonFormatter(logging.Formatter):
    def __init__(self, base: dict):
        super().__init__()
        self.base = base

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "@timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "message": Style.strip(record.getMessage()),
            **self.base,
            **getattr(record, "slop", {}),
        }
        return json.dumps(entry, default=str)
