from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import CrawlConfig, ProductRecord


@dataclass
class Checkpoint:
    config: CrawlConfig
    current_page: int
    raw_records: int
    status: str
    records: list[ProductRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "config": self.config.to_dict(),
            "current_page": self.current_page,
            "raw_records": self.raw_records,
            "status": self.status,
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        if int(data.get("version", 0)) != 1:
            raise ValueError("不支持的检查点版本")
        return cls(
            config=CrawlConfig.from_dict(data["config"]),
            current_page=int(data["current_page"]),
            raw_records=int(data["raw_records"]),
            status=str(data["status"]),
            records=[ProductRecord.from_dict(item) for item in data.get("records", [])],
        )


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_config(cls, config: CrawlConfig) -> CheckpointStore:
        digest = hashlib.sha1(config.keyword.encode("utf-8")).hexdigest()[:12]
        return cls(config.output_dir / ".goofish-checkpoints" / f"task-{digest}.json")

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Checkpoint | None:
        if not self.exists():
            return None
        with self.path.open("r", encoding="utf-8") as handle:
            return Checkpoint.from_dict(json.load(handle))

    def save(self, checkpoint: Checkpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(checkpoint.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.flush()
        temporary.replace(self.path)

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

