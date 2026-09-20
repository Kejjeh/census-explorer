from __future__ import annotations

import contextlib
import struct
import tempfile
import zipfile
from pathlib import Path

from census_explorer import http_client

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures"


@contextlib.contextmanager
def offline():
    """Fail loudly if the code under test tries to use the network."""
    previous = http_client.is_offline()
    http_client.set_offline(True)
    try:
        yield
    finally:
        http_client.set_offline(previous)


@contextlib.contextmanager
def temp_root():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def write_shapefile_zip(path: Path, polygons: list[tuple[str, list[tuple[float, float]]]],
                        geoid_field: str = "GEOID") -> Path:
    """Write a minimal but valid polygon shapefile + dBASE pair into a zip.

    Used to exercise the reader (and its failure modes) without shipping a
    binary fixture or depending on a GIS library.  The rings are written
    clockwise, as the shapefile specification requires for an outer ring.
    """
    shp_records = b""
    for index, (_geoid, ring) in enumerate(polygons, start=1):
        pts = list(ring)
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        content = struct.pack("<i", 5)
        content += struct.pack("<4d", min(xs), min(ys), max(xs), max(ys))
        content += struct.pack("<ii", 1, len(pts))
        content += struct.pack("<i", 0)
        for x, y in pts:
            content += struct.pack("<2d", x, y)
        shp_records += struct.pack(">ii", index, len(content) // 2) + content

    header = struct.pack(">i", 9994) + b"\x00" * 20
    header += struct.pack(">i", (100 + len(shp_records)) // 2)
    header += struct.pack("<ii", 1000, 5)
    header += struct.pack("<4d", -180, -90, 180, 90)
    header += struct.pack("<4d", 0, 0, 0, 0)
    shp = header + shp_records

    field_len = 16
    record_len = 1 + field_len
    header_len = 32 + 32 + 1
    dbf = struct.pack("<4B", 3, 125, 1, 1)
    dbf += struct.pack("<IHH", len(polygons), header_len, record_len)
    dbf += b"\x00" * 20
    name = geoid_field.encode("ascii")[:11].ljust(11, b"\x00")
    dbf += name + b"C" + b"\x00" * 4 + bytes([field_len]) + b"\x00" * 15
    dbf += b"\x0d"
    for geoid, _ring in polygons:
        dbf += b" " + geoid.encode("ascii").ljust(field_len)[:field_len]
    dbf += b"\x1a"

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("test.shp", shp)
        zf.writestr("test.dbf", dbf)
    return path


def square(cx: float, cy: float, size: float = 0.1) -> list[tuple[float, float]]:
    """A clockwise square ring."""
    h = size / 2
    return [(cx - h, cy - h), (cx - h, cy + h), (cx + h, cy + h), (cx + h, cy - h)]
