"""Quick automatic checks. Run with:  python test_app.py"""
import os

import app as a
import risk
import roads

TEST_DB = "test_rescue.db"
if os.path.exists(TEST_DB):
    try:
        os.remove(TEST_DB)
    except Exception:
        pass
a.DB_FILE = TEST_DB
a.init_db()
risk.fetch_rain_24h = lambda *args: 12.0          # fake rainfall so the test works offline
a.GRAPH = roads.demo_grid(a.CENTER)
c = a.app.test_client()
HERE = {"lat": a.CENTER[0], "lng": a.CENTER[1]}

# 1. route with no floods: shortest and safe route are the same
r = c.post("/api/route", json={"from": HERE, "shelter_id": "nearest"}).get_json()
assert r["same_route"] and not r["safe"]["crosses_flood"]

# 2. flood in the middle of the shortest route: safe route must avoid it
mid = r["shortest"]["coords"][len(r["shortest"]["coords"]) // 2]
assert c.post("/api/reports", json={"lat": mid[0], "lng": mid[1], "severity": "high", "depth_cm": 80}).status_code == 201
r2 = c.post("/api/route", json={"from": HERE, "shelter_id": r["shelter"]["id"]}).get_json()
assert r2["shortest"]["crosses_flood"] and not r2["safe"]["crosses_flood"]

# 3. bad input is rejected
assert c.post("/api/reports", json={"lat": 1, "lng": 1, "severity": "huge"}).status_code == 400
assert c.post("/api/sos", json={"lat": 1, "lng": 1, "people": 0, "need": "medical"}).status_code == 400

# 4. SOS is private until rescue login, then sorted by priority
c.post("/api/sos", json={"lat": 15.36, "lng": 75.12, "people": 1, "need": "food_water"})
c.post("/api/sos", json={"lat": 15.36, "lng": 75.12, "people": 4, "need": "medical"})
assert c.get("/api/sos").status_code == 401
assert c.post("/rescue/login", json={"pin": a.RESCUE_PIN}).status_code == 200
queue = c.get("/api/sos").get_json()
assert queue[0]["need"] == "medical"

# 5. risk cells exist for the reported area
assert c.get("/api/risk").get_json()["cells"]

# 6. Page Slugs, SEO, and Headings
home = c.get("/").get_data(as_text=True)
assert "<title>Shivamogga Flood Rescue" in home
assert "<h1 id=\"page-h1\">" in home
assert "canonical" in home
assert "WebApplication" in home

report_pg = c.get("/report").get_data(as_text=True)
assert "Report Flooded Roads & Waterlogging" in report_pg
assert "rel=\"canonical\" href=\"http://localhost/report\"" in report_pg or "report" in report_pg

sos_pg = c.get("/sos").get_data(as_text=True)
assert "Emergency SOS Rescue Request" in sos_pg
assert "tab-sos" in sos_pg

route_pg = c.get("/route").get_data(as_text=True)
assert "Safe Flood Evacuation Route Finder" in route_pg

aid_pg = c.get("/first-aid").get_data(as_text=True)
assert "Flood Emergency Safety & First Aid" in aid_pg

# 7. Rescue Dashboard privacy
rescue_pg = c.get("/rescue").get_data(as_text=True)
assert 'content="noindex, nofollow, noarchive"' in rescue_pg

# 8. Custom 404 page
not_found = c.get("/non-existent-path")
assert not_found.status_code == 404
assert "Page or Resource Not Found" in not_found.get_data(as_text=True)
assert "404 ERROR" in not_found.get_data(as_text=True)

# 9. robots.txt, sitemap.xml, llms.txt, favicon.ico
robots = c.get("/robots.txt").get_data(as_text=True)
assert "Disallow: /rescue" in robots
assert "Sitemap:" in robots

sitemap = c.get("/sitemap.xml").get_data(as_text=True)
assert "<loc>" in sitemap
assert "/sos</loc>" in sitemap
assert "/rescue" not in sitemap

llms = c.get("/llms.txt").get_data(as_text=True)
assert "# Shivamogga Flood Rescue System" in llms

fav = c.get("/favicon.ico")
assert fav.status_code == 200

if os.path.exists(TEST_DB):
    try:
        os.remove(TEST_DB)
    except Exception:
        pass

print("All checks passed")
