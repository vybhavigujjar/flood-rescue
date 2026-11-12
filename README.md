# Flood Rescue and Safe Route Finder

A real-time web platform: citizens report floods and send SOS requests, rescue teams get a priority queue,
and a route engine finds a path to a shelter that avoids flooded roads. Software only, no hardware.

## Run it
1. Install Python 3.9 or newer.
2. In this folder: `python -m venv venv`, then activate it
   (Windows: `venv\Scripts\activate`, Mac/Linux: `source venv/bin/activate`).
3. `pip install -r requirements.txt`
4. `python app.py` (the first start downloads roads from OpenStreetMap and saves them to roads_cache.json,
   this can take a minute. If the download fails the app uses a demo road grid so you can still try everything.)
5. Citizens:
   - Live Map: http://127.0.0.1:5000/
   - Report Flood: http://127.0.0.1:5000/report
   - Emergency SOS: http://127.0.0.1:5000/sos
   - Safe Evacuation Route: http://127.0.0.1:5000/route
   - Flood Safety & First Aid: http://127.0.0.1:5000/first-aid
   Rescue team: http://127.0.0.1:5000/rescue (default PIN 1234)
6. Optional self-check: `python test_app.py`

### Configuration
- `BASE_URL`: Base web URL (e.g., `https://flood-rescue-shivamogga.onrender.com`). Defaults dynamically to the current host (`http://127.0.0.1:5000` or Render/ngrok domain). Used for canonical URLs, sitemap.xml, and Open Graph social sharing tags.
- `CENTER_LAT`, `CENTER_LNG`: Latitude and longitude for the map center (default: Shivamogga, Karnataka `13.9299`, `75.5681`). Delete `roads_cache.json` after changing.
- `RESCUE_PIN`: Access PIN for the rescue team command dashboard (default: `1234`).
- `DB_FILE`: SQLite database path (default: `rescue.db`).

## Files & Architecture
- `app.py`: Flask application server, clean page slugs, SEO endpoints (`/robots.txt`, `/sitemap.xml`, `/llms.txt`), WebSocket broadcasts, and REST APIs.
- `roads.py`: Road network graph from OpenStreetMap + custom A* shortest-path search.
- `risk.py`: Rainfall accumulation (Open-Meteo API), hazard zone penalties, and risk score calculation.
- `shelters.json`: Designated emergency shelters and medical relief centers.
- `static/`: Brand assets including `favicon.svg`, `favicon.ico`, `og-image.png`, and `og-image.svg`.
- `templates/index.html`: Public citizen application with clean page slugs, Schema.org structured data, breadcrumbs, and interactive Leaflet map.
- `templates/rescue.html`: Authorized rescue team dashboard (configured with `noindex` privacy).
- `templates/404.html`: Custom emergency-friendly 404 error page.
- `test_app.py`: Automated test suite covering routing, SOS queues, risk scoring, and SEO endpoints.

## How the safe route works
1. Roads are a graph: junctions are nodes, road pieces are edges with a length in metres.
2. Each active flood report affects roads within a radius (low 60 m, medium 120 m, high 200 m).
3. Roads in a zone cost more: low x2, medium x8, high x1000 (so a high flood is avoided unless there is no other way).
4. A* finds the cheapest path. The straight-line distance is the guide, and it never overestimates because costs are never below the real length.
5. The result is compared with the normal shortest route so you can show both on the map.
6. If the only path still crosses a high flood, the app warns the user instead of calling it safe.

## How the risk score works
Per 1 km grid cell: sum of report points (low 5, medium 12, high 25, multiplied by 1 + depth/50) plus rainfall points
(0.5 per mm of rain in the last 24 hours, max 30). Capped at 100. Under 25 low, 25 to 54 medium, 55 and above high.
This is a simple, explainable rule set, not a forecast.

## Limits to mention honestly in your report
- Flood reports come from users and are not verified. Reports expire after 24 hours or when rescue staff mark them resolved.
- Roads are treated as two-way and flood depth is not measured, only reported.
- The PIN login is basic. A real system needs proper accounts (future scope).
- Shelter data is demo data until you replace it.
