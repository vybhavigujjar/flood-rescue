import contextlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, jsonify, render_template, request, send_from_directory, session
from flask_socketio import SocketIO, join_room

import risk
import roads

DB_FILE = os.environ.get("DB_FILE", "rescue.db")
CENTER = (float(os.environ.get("CENTER_LAT", 13.9299)), float(os.environ.get("CENTER_LNG", 75.5681)))
RESCUE_PIN = os.environ.get("RESCUE_PIN", "1234")       # change this before a real demo
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")   # configurable canonical base URL
REPORT_LIFETIME_HOURS = 24

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
socketio = SocketIO(app, async_mode="threading")
GRAPH = None   # road graph, loaded when the server starts


# ---------------- base URL & context ----------------

def get_base_url():
    if BASE_URL:
        return BASE_URL
    try:
        return request.url_root.rstrip("/")
    except Exception:
        return "http://localhost:5000"


@app.context_processor
def inject_globals():
    return {
        "BASE_URL": get_base_url(),
    }


# ---------------- database helpers ----------------

@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, lat REAL NOT NULL, lng REAL NOT NULL,
            severity TEXT NOT NULL, depth_cm INTEGER NOT NULL, note TEXT,
            resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS sos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, lat REAL NOT NULL, lng REAL NOT NULL,
            people INTEGER NOT NULL, need TEXT NOT NULL, contact TEXT, note TEXT,
            status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL)""")


def now_str(delta_hours=0):
    t = datetime.now(timezone.utc) - timedelta(hours=delta_hours)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def parse_time(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def report_dict(row):
    d = {k: row[k] for k in row.keys()}
    d["radius_m"] = risk.RADIUS_M[d["severity"]]
    return d


def active_reports():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM reports WHERE resolved = 0 AND created_at >= ? ORDER BY id DESC",
                            (now_str(REPORT_LIFETIME_HOURS),)).fetchall()
    return [report_dict(r) for r in rows]


NEED_POINTS = {"medical": 40, "trapped": 35, "food_water": 15, "other": 5}


def sos_dict(row):
    d = {k: row[k] for k in row.keys()}
    minutes = (datetime.now(timezone.utc) - parse_time(d["created_at"])).total_seconds() / 60
    # priority = how urgent the need is + number of people + how long they have waited
    d["priority"] = int(NEED_POINTS.get(d["need"], 5) + min(25, d["people"] * 5) + min(20, minutes / 5))
    return d


def get_point(data, key=None):
    src = data.get(key) if key else data
    lat, lng = float(src["lat"]), float(src["lng"])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("location out of range")
    return lat, lng


def rescue_only():
    return session.get("rescue") is True


# ---------------- page metadata ----------------

PAGES_META = {
    "index": {
        "slug": "/",
        "tab": "report",
        "title": "Shivamogga Flood Rescue & Live Flood Map | Emergency Evacuation",
        "h1": "Shivamogga Flood Rescue & Safety Map",
        "sub": "Real-time flood reporting, emergency SOS beacons, and safe evacuation route navigation.",
        "description": "Live flood monitoring, emergency rescue dispatch, and flood-avoiding evacuation routing for Shivamogga, Karnataka.",
        "breadcrumb": [{"name": "Home", "url": "/"}],
    },
    "report": {
        "slug": "/report",
        "tab": "report",
        "title": "Report Flooded Roads & Waterlogging | Shivamogga Flood Rescue",
        "h1": "Report Flooded Roads & Waterlogging",
        "sub": "Submit observed flood depths and road blockages to guide emergency response.",
        "description": "Report flooded roads and waterlogging depth in Shivamogga. Updates appear immediately on the live map and evacuation routing.",
        "breadcrumb": [{"name": "Home", "url": "/"}, {"name": "Report Flood", "url": "/report"}],
    },
    "sos": {
        "slug": "/sos",
        "tab": "sos",
        "title": "Emergency Flood SOS & Rescue Request | Shivamogga Flood Rescue",
        "h1": "Emergency SOS Rescue Request",
        "sub": "Send your location and rescue needs directly to field rescue teams.",
        "description": "Urgent emergency rescue request for individuals trapped by floodwaters or requiring medical help in Shivamogga.",
        "breadcrumb": [{"name": "Home", "url": "/"}, {"name": "SOS Request", "url": "/sos"}],
    },
    "route": {
        "slug": "/route",
        "tab": "route",
        "title": "Safe Evacuation Route Finder | Shivamogga Flood Rescue",
        "h1": "Safe Flood Evacuation Route Finder",
        "sub": "Find dry road paths to the nearest relief shelters avoiding flooded areas.",
        "description": "Calculate dry evacuation routes to designated flood relief shelters in Shivamogga avoiding reported high flood risks.",
        "breadcrumb": [{"name": "Home", "url": "/"}, {"name": "Safe Route", "url": "/route"}],
    },
    "aid": {
        "slug": "/first-aid",
        "tab": "aid",
        "title": "Flood Safety Guidelines & First Aid | Shivamogga Flood Rescue",
        "h1": "Flood Emergency Safety & First Aid",
        "sub": "Essential survival tips, water safety rules, and emergency helplines.",
        "description": "Life-saving flood guidelines: emergency contacts (112, 108), precautions against contaminated water, and electrical safety.",
        "breadcrumb": [{"name": "Home", "url": "/"}, {"name": "First Aid", "url": "/first-aid"}],
    },
}


# ---------------- pages & SEO ----------------

@app.route("/")
def index():
    return render_template("index.html", page=PAGES_META["index"])


@app.route("/report")
def report_page():
    return render_template("index.html", page=PAGES_META["report"])


@app.route("/sos")
def sos_page():
    return render_template("index.html", page=PAGES_META["sos"])


@app.route("/route")
def route_page():
    return render_template("index.html", page=PAGES_META["route"])


@app.route("/first-aid")
def first_aid_page():
    return render_template("index.html", page=PAGES_META["aid"])


@app.route("/rescue")
def rescue_page():
    return render_template("rescue.html")


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.route("/robots.txt")
def robots_txt():
    base = get_base_url()
    content = (
        "User-agent: *\n"
        "Disallow: /rescue\n"
        "Disallow: /rescue/*\n"
        "Disallow: /api/\n"
        "Allow: /\n\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain; charset=utf-8")


@app.route("/sitemap.xml")
def sitemap_xml():
    base = get_base_url()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [
        {"loc": f"{base}/", "priority": "1.0", "changefreq": "hourly"},
        {"loc": f"{base}/sos", "priority": "0.9", "changefreq": "hourly"},
        {"loc": f"{base}/report", "priority": "0.9", "changefreq": "hourly"},
        {"loc": f"{base}/route", "priority": "0.8", "changefreq": "daily"},
        {"loc": f"{base}/first-aid", "priority": "0.7", "changefreq": "weekly"},
    ]
    items = [
        f"  <url>\n    <loc>{u['loc']}</loc>\n    <lastmod>{now_iso}</lastmod>\n    <changefreq>{u['changefreq']}</changefreq>\n    <priority>{u['priority']}</priority>\n  </url>"
        for u in urls
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(items) + "\n</urlset>"
    return Response(xml, mimetype="application/xml; charset=utf-8")


@app.route("/llms.txt")
def llms_txt():
    content = (
        "# Shivamogga Flood Rescue System\n\n"
        "> Real-time flood monitoring, crowdsourced incident reporting, emergency SOS dispatch, and flood-avoiding evacuation routing for Shivamogga, Karnataka, India.\n\n"
        "## Public Web Endpoints\n"
        "- / : Interactive live flood map with waterlogged areas, OpenMeteo rain risk heatmap, and designated shelter locations.\n"
        "- /report : Public portal for reporting flooded streets, water depth (cm), and road blockage observations.\n"
        "- /sos : Emergency SOS beacon submission for residents trapped by floodwaters or needing medical/food aid.\n"
        "- /route : A* evacuation route calculator finding the shortest dry path to emergency shelters avoiding high-risk flood zones.\n"
        "- /first-aid : Emergency survival precautions, electrical hazard safety, and key emergency helplines (112, 108).\n\n"
        "## Technical Architecture & APIs\n"
        "- Framework: Python Flask with Flask-SocketIO (WebSocket real-time push events).\n"
        "- Database: SQLite tracking active reports (24-hour decay) and priority-weighted SOS queues.\n"
        "- Map & Routing: Leaflet.js with OpenStreetMap road network and custom A* pathfinding.\n"
        "- Weather: Open-Meteo precipitation API calculating localized 24-hour rain accumulation.\n"
    )
    return Response(content, mimetype="text/plain; charset=utf-8")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(app.root_path, "static"), "favicon.ico", mimetype="image/x-icon")


# ---------------- public API ----------------

@app.route("/api/config")
def api_config():
    return jsonify({"center": CENTER, "road_source": GRAPH.source if GRAPH else "not loaded"})


@app.route("/api/shelters")
def api_shelters():
    with open(os.path.join(os.path.dirname(__file__), "shelters.json")) as f:
        return jsonify(json.load(f))


@app.route("/api/reports", methods=["GET"])
def list_reports():
    return jsonify(active_reports())


@app.route("/api/reports", methods=["POST"])
def add_report():
    data = request.get_json(silent=True) or {}
    try:
        lat, lng = get_point(data)
        depth = int(data.get("depth_cm", 0))
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "location and depth must be valid numbers"}), 400
    severity = data.get("severity")
    if severity not in risk.RADIUS_M:
        return jsonify({"error": "severity must be low, medium or high"}), 400
    if not 0 <= depth <= 1000:
        return jsonify({"error": "depth must be between 0 and 1000 cm"}), 400
    note = str(data.get("note", ""))[:300]
    with get_db() as conn:
        cur = conn.execute("INSERT INTO reports (lat, lng, severity, depth_cm, note, created_at) VALUES (?,?,?,?,?,?)",
                           (lat, lng, severity, depth, note, now_str()))
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (cur.lastrowid,)).fetchone()
    report = report_dict(row)
    socketio.emit("new_report", report)
    return jsonify(report), 201


@app.route("/api/sos", methods=["POST"])
def add_sos():
    data = request.get_json(silent=True) or {}
    try:
        lat, lng = get_point(data)
        people = int(data.get("people", 1))
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "location and number of people must be valid"}), 400
    need = data.get("need")
    if need not in NEED_POINTS:
        return jsonify({"error": "need must be medical, trapped, food_water or other"}), 400
    if not 1 <= people <= 200:
        return jsonify({"error": "people must be between 1 and 200"}), 400
    contact = str(data.get("contact", ""))[:60]
    note = str(data.get("note", ""))[:300]
    with get_db() as conn:
        cur = conn.execute("INSERT INTO sos (lat, lng, people, need, contact, note, created_at) VALUES (?,?,?,?,?,?,?)",
                           (lat, lng, people, need, contact, note, now_str()))
        row = conn.execute("SELECT * FROM sos WHERE id = ?", (cur.lastrowid,)).fetchone()
    # SOS details go only to logged-in rescue staff, never to the public
    socketio.emit("new_sos", sos_dict(row), room="rescue")
    return jsonify({"id": row["id"], "message": "SOS received. Stay where you are and keep your phone charged."}), 201


@app.route("/api/risk")
def api_risk():
    rain = risk.fetch_rain_24h(*CENTER)
    return jsonify({"rain_24h_mm": rain, "cells": risk.score_cells(active_reports(), rain)})


@app.route("/api/route", methods=["POST"])
def api_route():
    data = request.get_json(silent=True) or {}
    if GRAPH is None:
        return jsonify({"error": "road map is not loaded yet"}), 503
    try:
        lat, lng = get_point(data, "from")
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "choose a starting point first"}), 400

    start, gap = GRAPH.nearest_node(lat, lng)
    if start is None or gap > 1000:
        return jsonify({"error": "that point is outside the loaded road area"}), 400

    with open(os.path.join(os.path.dirname(__file__), "shelters.json")) as f:
        shelters = json.load(f)
    wanted = data.get("shelter_id")
    if wanted not in (None, "nearest"):
        shelters = [s for s in shelters if s["id"] == wanted]
        if not shelters:
            return jsonify({"error": "unknown shelter"}), 400

    penalty = risk.make_penalty(GRAPH, active_reports())
    best = None   # (cost, shelter, goal node, path, length)
    for s in shelters:
        goal, gap2 = GRAPH.nearest_node(s["lat"], s["lng"])
        if goal is None or gap2 > 1000:
            continue
        found = roads.astar(GRAPH, start, goal, penalty)
        if not found:
            continue
        path, length = found
        # cost counts flooded roads as longer, so a dry route to a farther shelter can win
        cost = sum(roads.meters(*GRAPH.nodes[a], *GRAPH.nodes[b]) * penalty(a, b)
                   for a, b in zip(path, path[1:]))
        if best is None or cost < best[0]:
            best = (cost, s, goal, path, length)
    if best is None:
        return jsonify({"error": "no route found to a shelter"}), 404
    _, shelter, goal, safe_path, safe_len = best
    short_path, short_len = roads.astar(GRAPH, start, goal)   # normal shortest route, ignoring floods

    def as_route(path, length):
        return {"coords": [list(GRAPH.nodes[n]) for n in path],
                "distance_m": round(length),
                "crosses_flood": risk.crosses_flood(path, penalty)}

    safe = as_route(safe_path, safe_len)
    shortest = as_route(short_path, short_len)
    return jsonify({"shelter": shelter, "safe": safe, "shortest": shortest,
                    "same_route": safe_path == short_path})


# ---------------- rescue team API ----------------

@app.route("/rescue/login", methods=["POST"])
def rescue_login():
    pin = (request.get_json(silent=True) or {}).get("pin", "")
    if pin == RESCUE_PIN:
        session["rescue"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "wrong PIN"}), 401


@app.route("/rescue/logout", methods=["POST"])
def rescue_logout():
    session.pop("rescue", None)
    return jsonify({"ok": True})


@app.route("/api/sos", methods=["GET"])
def list_sos():
    if not rescue_only():
        return jsonify({"error": "rescue login required"}), 401
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM sos WHERE status != 'rescued'").fetchall()
    return jsonify(sorted((sos_dict(r) for r in rows), key=lambda d: -d["priority"]))


@app.route("/api/sos/<int:sos_id>", methods=["PATCH"])
def update_sos(sos_id):
    if not rescue_only():
        return jsonify({"error": "rescue login required"}), 401
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("open", "assigned", "rescued"):
        return jsonify({"error": "status must be open, assigned or rescued"}), 400
    with get_db() as conn:
        conn.execute("UPDATE sos SET status = ? WHERE id = ?", (status, sos_id))
        row = conn.execute("SELECT * FROM sos WHERE id = ?", (sos_id,)).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    socketio.emit("sos_update", sos_dict(row), room="rescue")
    return jsonify(sos_dict(row))


@app.route("/api/reports/<int:report_id>/resolve", methods=["POST"])
def resolve_report(report_id):
    if not rescue_only():
        return jsonify({"error": "rescue login required"}), 401
    with get_db() as conn:
        conn.execute("UPDATE reports SET resolved = 1 WHERE id = ?", (report_id,))
    socketio.emit("report_resolved", {"id": report_id})
    return jsonify({"ok": True})


@socketio.on("join_rescue")
def on_join_rescue():
    if rescue_only():
        join_room("rescue")


if __name__ == "__main__":
    init_db()
    print("Loading roads (first run downloads them, this can take a minute)...")
    GRAPH = roads.load_graph(CENTER)
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
