"""Flood risk scoring and flooded-zone penalties for routing."""
import math
import time
from datetime import datetime, timezone

import requests

CELL = 0.01                                    # grid cell size in degrees (about 1.1 km)
RADIUS_M = {"low": 60, "medium": 120, "high": 200}   # how far a report affects nearby roads
PENALTY = {"low": 2.0, "medium": 8.0, "high": 1000.0}  # road cost multiplier inside that radius
POINTS = {"low": 5, "medium": 12, "high": 25}  # risk points per report
HIGH_BLOCK = 1000.0

_rain_cache = {"time": 0, "key": None, "value": None}


def fetch_rain_24h(lat, lng):
    """Rain in the last 24 hours (mm) from the free Open-Meteo API. Returns None if unavailable."""
    key = (round(lat, 2), round(lng, 2))
    if _rain_cache["key"] == key and time.time() - _rain_cache["time"] < 900:
        return _rain_cache["value"]
    value = None
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lng, "hourly": "precipitation",
                    "past_days": 1, "forecast_days": 1, "timezone": "UTC"},
            timeout=8,
        )
        resp.raise_for_status()
        hourly = resp.json()["hourly"]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        total = 0.0
        for t, mm in zip(hourly["time"], hourly["precipitation"]):
            when = datetime.strptime(t, "%Y-%m-%dT%H:%M")
            if 0 <= (now - when).total_seconds() <= 24 * 3600 and mm is not None:
                total += mm
        value = round(total, 1)
    except (requests.RequestException, KeyError, ValueError):
        value = None
    _rain_cache.update(time=time.time(), key=key, value=value)
    return value


def level_for(score):
    if score >= 55:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def score_cells(reports, rain_mm):
    """Risk score (0-100) for each grid cell that has at least one active report.
    score = report points (severity weight x depth factor) + rainfall points (max 30)."""
    rain_points = min(30.0, (rain_mm or 0) * 0.5)
    cells = {}
    for r in reports:
        key = (math.floor(r["lat"] / CELL), math.floor(r["lng"] / CELL))
        pts = POINTS[r["severity"]] * (1 + r["depth_cm"] / 50.0)
        cell = cells.setdefault(key, {"points": 0.0, "reports": 0})
        cell["points"] += pts
        cell["reports"] += 1
    out = []
    for (i, j), c in cells.items():
        score = min(100, round(c["points"] + rain_points))
        out.append({"lat_min": i * CELL, "lng_min": j * CELL,
                    "lat_max": (i + 1) * CELL, "lng_max": (j + 1) * CELL,
                    "score": score, "level": level_for(score), "reports": c["reports"]})
    return out


def _near(lat, lng, zlat, zlng, radius):
    dy = (lat - zlat) * 111320.0
    dx = (lng - zlng) * 111320.0 * math.cos(math.radians(zlat))
    return dx * dx + dy * dy <= radius * radius


def make_penalty(graph, reports):
    """Returns penalty(u, v): cost multiplier for the road between nodes u and v.
    A road touches a flood zone if either end or its midpoint is inside the zone's radius."""
    zones = [(r["lat"], r["lng"], RADIUS_M[r["severity"]], PENALTY[r["severity"]]) for r in reports]
    cache = {}

    def penalty(u, v):
        key = (u, v) if u < v else (v, u)
        if key in cache:
            return cache[key]
        a, b = graph.nodes[u], graph.nodes[v]
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        worst = 1.0
        for zlat, zlng, radius, pen in zones:
            if pen > worst and any(_near(p[0], p[1], zlat, zlng, radius) for p in (a, mid, b)):
                worst = pen
        cache[key] = worst
        return worst

    return penalty


def crosses_flood(path, penalty):
    """True if any road on the path goes through a high-severity flood zone."""
    return any(penalty(a, b) >= HIGH_BLOCK for a, b in zip(path, path[1:]))
