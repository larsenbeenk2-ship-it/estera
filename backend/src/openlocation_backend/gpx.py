"""Bounded GPX import/export. Segment selection is always explicit."""
from datetime import datetime, timezone
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException

import gpxpy
import gpxpy.gpx
from defusedxml import ElementTree

from .models import MAX_GPX_BYTES, MAX_POINTS, Point, Route


def segments(xml: str) -> list[dict]:
    try:
        return _segments(xml)
    except (ParseError, DefusedXmlException, gpxpy.gpx.GPXException) as exc:
        raise ValueError("Invalid or unsafe GPX document") from exc


def _segments(xml: str) -> list[dict]:
    encoded = xml.encode("utf-8")
    if len(encoded) > MAX_GPX_BYTES:
        raise ValueError("GPX exceeds 5 MiB")
    root = ElementTree.fromstring(encoded, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    if root.tag.rsplit("}", 1)[-1] != "gpx":
        raise ValueError("expected a GPX document")
    # Validate XML before gpxpy sees it; prevent entity/DTD handling even if its parser changes.
    if sum(1 for e in root.iter() if e.tag.rsplit("}", 1)[-1] in {"trkpt", "rtept", "wpt"}) > MAX_POINTS:
        raise ValueError("GPX exceeds 50,000 points")
    doc = gpxpy.parse(xml)
    result = []
    for track in doc.tracks:
        for segment in track.segments:
            result.append(_segment(track.name or "GPX track", segment.points, len(result)))
    for route in doc.routes:
        result.append(_segment(route.name or "GPX route", route.points, len(result)))
    if not result:
        raise ValueError("GPX contains no track or route segments")
    return result


def _segment(name, points, index):
    parsed = []
    for p in points:
        timestamp = p.time
        if timestamp is not None and timestamp.tzinfo is None:
            raise ValueError("GPX timestamps need a timezone")
        parsed.append(Point(latitude=p.latitude, longitude=p.longitude, time=None if timestamp is None else timestamp.timestamp()).model_dump())
    try:
        # Offer exactly the timing interpretation that import will accept.
        Route(points=[Point(**p) for p in parsed], source="gpx", timing="recorded")
        valid = True
    except ValueError:
        valid = False
    return {"index": index, "name": name[:200], "points": parsed, "recorded_timing_available": valid}


def import_segment(xml: str, index: int, timing="constant") -> Route:
    found = segments(xml)
    if type(index) is not int or not 0 <= index < len(found):
        raise ValueError("choose a valid segment index from gpx.inspect")
    selected = found[index]
    return Route(name=selected["name"], points=[Point(**p) for p in selected["points"]], source="gpx", timing=timing)


def export(route: Route) -> str:
    doc = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=route.name)
    segment = gpxpy.gpx.GPXTrackSegment()
    for p in route.points:
        segment.points.append(gpxpy.gpx.GPXTrackPoint(p.latitude, p.longitude,
                              time=None if p.time is None else datetime.fromtimestamp(p.time, timezone.utc)))
    track.segments.append(segment)
    doc.tracks.append(track)
    output = doc.to_xml()
    if len(output.encode()) > MAX_GPX_BYTES:
        raise ValueError("export exceeds 5 MiB")
    return output
