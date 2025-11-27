import base64
import json
from typing import Any


def _encode_cursor(index: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"i": index}).encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return int(data.get("i", 0))
    except Exception:
        return 0


def paginate_list(
    items: list[Any], cursor: str | None, limit: int
) -> tuple[list[Any], str | None]:
    start = _decode_cursor(cursor) if cursor else 0
    end = max(0, start) + max(0, limit)
    page = items[start:end]
    next_cursor: str | None = _encode_cursor(end) if end < len(items) else None
    return page, next_cursor


def downsample_time_series(
    points: list[Any], max_points: int
) -> tuple[list[Any], bool]:
    if max_points <= 0 or len(points) <= max_points:
        return points, False
    step = len(points) / max_points
    sampled = [points[int(i * step)] for i in range(max_points)]
    return sampled, True
