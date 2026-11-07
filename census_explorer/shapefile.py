"""A minimal, dependency-free reader for Census cartographic boundary files.

Only what this project needs: the polygon shape type and the attribute table,
read straight out of the published ``.zip`` and converted to GeoJSON.  Writing
this by hand keeps the project on the standard library; the trade-off is that
unsupported shape types are rejected rather than approximated.

Format references are the public ESRI shapefile and dBASE specifications; the
code validates the file signature rather than trusting the extension.
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SHAPE_NULL = 0
SHAPE_POLYGON = 5
SUPPORTED_SHAPE_TYPES = {SHAPE_NULL, SHAPE_POLYGON}


class ShapefileError(ValueError):
    pass


@dataclass
class Feature:
    properties: dict[str, Any]
    geometry: dict[str, Any] | None


def _ring_signed_area(ring: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _read_polygon(content: memoryview) -> dict[str, Any] | None:
    # shape type already consumed by the caller
    # Record content: shape type (4) + bounding box (32) = 40 bytes before the
    # part/point counts.
    num_parts, num_points = struct.unpack_from("<ii", content, 36)
    if num_parts <= 0 or num_points <= 0:
        return None
    parts = struct.unpack_from(f"<{num_parts}i", content, 44)
    offset = 44 + 4 * num_parts
    coords = struct.unpack_from(f"<{2 * num_points}d", content, offset)

    rings: list[list[tuple[float, float]]] = []
    bounds = list(parts) + [num_points]
    for i in range(num_parts):
        start, end = bounds[i], bounds[i + 1]
        ring = [(coords[2 * j], coords[2 * j + 1]) for j in range(start, end)]
        if len(ring) < 4:
            continue
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rings.append(ring)
    if not rings:
        return None

    # In a shapefile an outer ring is clockwise (negative signed area with the
    # standard shoelace convention) and a hole is counter-clockwise.
    polygons: list[list[list[list[float]]]] = []
    for ring in rings:
        as_lists = [[x, y] for x, y in ring]
        if _ring_signed_area(ring) < 0 or not polygons:
            polygons.append([as_lists])
        else:
            polygons[-1].append(as_lists)

    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _iter_shapes(shp: bytes) -> Iterator[dict[str, Any] | None]:
    if len(shp) < 100:
        raise ShapefileError("shapefile is too short to contain a header")
    file_code, = struct.unpack_from(">i", shp, 0)
    if file_code != 9994:
        raise ShapefileError(f"not a shapefile: file code {file_code}")
    file_shape_type, = struct.unpack_from("<i", shp, 32)
    if file_shape_type not in SUPPORTED_SHAPE_TYPES:
        raise ShapefileError(
            f"unsupported shape type {file_shape_type}; this reader handles polygons only"
        )
    view = memoryview(shp)
    pos = 100
    total = len(shp)
    while pos + 8 <= total:
        _, content_words = struct.unpack_from(">ii", shp, pos)
        content_len = content_words * 2
        body = view[pos + 8: pos + 8 + content_len]
        pos += 8 + content_len
        if content_len < 4:
            yield None
            continue
        shape_type, = struct.unpack_from("<i", body, 0)
        if shape_type == SHAPE_NULL:
            yield None
        elif shape_type == SHAPE_POLYGON:
            yield _read_polygon(body)
        else:
            raise ShapefileError(f"unsupported shape type {shape_type} in record")


def _read_dbf(dbf: bytes) -> list[dict[str, str]]:
    if len(dbf) < 32:
        raise ShapefileError("dbf file is too short")
    num_records, header_len, record_len = struct.unpack_from("<IHH", dbf, 4)
    fields: list[tuple[str, int, str]] = []
    pos = 32
    while pos < header_len - 1:
        raw = dbf[pos:pos + 32]
        if not raw or raw[0] in (0x0D, 0x00):
            break
        name = raw[0:11].split(b"\x00")[0].decode("latin-1").strip()
        ftype = chr(raw[11])
        flen = raw[16]
        fields.append((name, flen, ftype))
        pos += 32

    rows: list[dict[str, str]] = []
    start = header_len
    for r in range(num_records):
        base = start + r * record_len
        rec = dbf[base:base + record_len]
        if len(rec) < record_len:
            break
        if rec[0:1] == b"*":       # deleted record
            rows.append({})
            continue
        offset = 1
        row: dict[str, str] = {}
        for name, flen, _ftype in fields:
            row[name] = rec[offset:offset + flen].decode("latin-1").strip()
            offset += flen
        rows.append(row)
    return rows


def read_zip(zip_path: str | Path) -> list[Feature]:
    """Read a Census cartographic boundary ``.zip`` into features."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
        dbf_name = next((n for n in names if n.lower().endswith(".dbf")), None)
        if not shp_name or not dbf_name:
            raise ShapefileError(f"{zip_path} does not contain a .shp and a .dbf")
        shp = zf.read(shp_name)
        dbf = zf.read(dbf_name)

    attributes = _read_dbf(dbf)
    geometries = list(_iter_shapes(shp))
    if len(attributes) != len(geometries):
        raise ShapefileError(
            f"attribute/geometry count mismatch in {zip_path}: "
            f"{len(attributes)} rows vs {len(geometries)} shapes"
        )
    return [Feature(properties=a, geometry=g) for a, g in zip(attributes, geometries)]


def round_geometry(geometry: dict[str, Any] | None, ndigits: int = 5) -> dict[str, Any] | None:
    """Round coordinates so the served GeoJSON stays small.

    Five decimal degrees is roughly a metre; the 1:500,000 cartographic files
    are already generalized well beyond that, so this loses no real precision.
    """
    if geometry is None:
        return None

    def walk(node):
        if isinstance(node, (int, float)):
            return round(float(node), ndigits)
        return [walk(x) for x in node]

    return {"type": geometry["type"], "coordinates": walk(geometry["coordinates"])}
