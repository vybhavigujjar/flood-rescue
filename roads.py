"""Road graph + A* search. Pure Python, no heavy libraries."""
import heapq
import json
import math
import os

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_FILE = "roads_cache.json"
ROAD_TYPES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street"


def meters(lat1, lng1, lat2, lng2):
    """Haversine distance between two points, in metres."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class RoadGraph:
    def __init__(self, source="unknown"):
        self.nodes = {}   # node id -> (lat, lng)
        self.adj = {}     # node id -> list of (neighbour id, length in metres)
        self.source = source

    def add_edge(self, a, b):
        if a == b or a not in self.nodes or b not in self.nodes:
            return
        d = meters(*self.nodes[a], *self.nodes[b])
        self.adj.setdefault(a, []).append((b, d))
        self.adj.setdefault(b, []).append((a, d))

    def nearest_node(self, lat, lng):
        """Closest road node to a point. Returns (node id, distance in metres)."""
        k = math.cos(math.radians(lat))
        best, best_d = None, float("inf")
        for nid in self.adj:
            la, ln = self.nodes[nid]
            d = (la - lat) ** 2 + ((ln - lng) * k) ** 2
            if d < best_d:
                best, best_d = nid, d
        if best is None:
            return None, float("inf")
        return best, meters(lat, lng, *self.nodes[best])

    def keep_largest_component(self):
        """Drop small disconnected road fragments so every node can reach every other."""
        seen, best = set(), set()
        for start in self.adj:
            if start in seen:
                continue
            comp, stack = set(), [start]
            while stack:
                u = stack.pop()
                if u in comp:
                    continue
                comp.add(u)
                stack.extend(v for v, _ in self.adj[u] if v not in comp)
            seen |= comp
            if len(comp) > len(best):
                best = comp
        self.adj = {u: [(v, d) for v, d in nb if v in best] for u, nb in self.adj.items() if u in best}


def astar(graph, start, goal, penalty=None):
    """A* search. `penalty(u, v)` returns a cost multiplier (>= 1) for an edge.
    Returns (list of node ids, total length in metres) or None if no path."""
    goal_pos = graph.nodes[goal]
    queue = [(0.0, start)]
    g = {start: 0.0}
    prev = {}
    done = set()
    while queue:
        _, u = heapq.heappop(queue)
        if u in done:
            continue
        if u == goal:
            path = [u]
            while u in prev:
                u = prev[u]
                path.append(u)
            path.reverse()
            length = sum(meters(*graph.nodes[a], *graph.nodes[b]) for a, b in zip(path, path[1:]))
            return path, length
        done.add(u)
        for v, length in graph.adj.get(u, []):
            if v in done:
                continue
            mult = max(1.0, penalty(u, v)) if penalty else 1.0
            ng = g[u] + length * mult
            if ng < g.get(v, float("inf")):
                g[v] = ng
                prev[v] = u
                h = meters(*graph.nodes[v], *goal_pos)   # straight-line distance never overestimates
                heapq.heappush(queue, (ng + h, v))
    return None


# ---------- building the graph ----------

def graph_from_overpass(data):
    g = RoadGraph(source="OpenStreetMap")
    for el in data.get("elements", []):
        if el.get("type") == "node":
            g.nodes[el["id"]] = (el["lat"], el["lon"])
    for el in data.get("elements", []):
        if el.get("type") == "way":
            ids = el.get("nodes", [])
            for a, b in zip(ids, ids[1:]):
                g.add_edge(a, b)
    g.keep_largest_component()
    return g


def demo_grid(center, size=25, spacing_m=150):
    """Fallback road grid (used if OpenStreetMap cannot be downloaded)."""
    g = RoadGraph(source="demo grid")
    lat0, lng0 = center
    dlat = spacing_m / 111320.0
    dlng = spacing_m / (111320.0 * math.cos(math.radians(lat0)))
    half = size // 2
    for i in range(size):
        for j in range(size):
            g.nodes[i * size + j] = (lat0 + (i - half) * dlat, lng0 + (j - half) * dlng)
    for i in range(size):
        for j in range(size):
            if j + 1 < size:
                g.add_edge(i * size + j, i * size + j + 1)
            if i + 1 < size:
                g.add_edge(i * size + j, (i + 1) * size + j)
    return g


def load_graph(center, radius_m=3000):
    """Use the cached OSM roads if present, otherwise download them, otherwise use the demo grid."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cached = json.load(f)
            if abs(cached["center"][0] - center[0]) < 1e-6 and abs(cached["center"][1] - center[1]) < 1e-6:
                return graph_from_overpass(cached["data"])
        except (OSError, ValueError, KeyError):
            pass
    query = (
        f'[out:json][timeout:90];way["highway"~"^({ROAD_TYPES})$"]'
        f"(around:{radius_m},{center[0]},{center[1]});out body;>;out skel qt;"
    )
    headers = {"User-Agent": "FloodRescueMiniProject/1.0 (college project)"}
    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        graph = graph_from_overpass(data)
        if len(graph.adj) < 50:
            raise ValueError("too few roads returned")
        with open(CACHE_FILE, "w") as f:
            json.dump({"center": list(center), "data": data}, f)
        return graph
    except (requests.RequestException, ValueError) as err:
        print("Could not download OpenStreetMap roads (" + str(err) + "). Using demo grid instead.")
        return demo_grid(center)