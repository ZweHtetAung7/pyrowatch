"""The "Ask PyroWatch" agent: Gemini with function calling over PyroWatch's own live data.

Gemini decides which tools to call (look up an area, compare two areas, find fires nearby,
predict satellite passes, check validation, read ground stations), we execute them against the
same code the map uses, feed the results back, and it answers from those results only. Every tool
call is returned to the UI as a visible trace.

Without GEMINI_API_KEY a tiny keyword router calls the same tools so the feature still demos.
"""
import logging
import re
from datetime import date

from . import ai, ground, orbits, risk
from .config import GEMINI_API_KEY, GEMINI_MODEL
from .geo import PLACES, cell_for_point, haversine_km

log = logging.getLogger("pyrowatch.agent")
MAX_STEPS = 6

AGENT_RULES = """
You can call tools that query PyroWatch's live data. Always call a tool before stating a number;
never guess. Resolve place names with find_area (it accepts towns, landmarks or "lat, lon").
When comparing areas, use compare_areas and explain the difference with factor points.
For "when will a satellite see X" use satellite_passes. Keep answers under 140 words, plain text,
with short "• " bullets when listing. If a tool reports demo/synthetic data, say so once."""

_S, _N, _I = {"type": "string"}, {"type": "number"}, {"type": "integer"}
DECLARATIONS = [
    {"name": "find_area", "description": "Look up one area's wildfire risk: score, category, statewide rank, factor-by-factor points, raw weather, recent fire detections and ground stations. Accepts a town or landmark name, an area id like r03c12, or 'lat, lon'.",
     "parameters": {"type": "object", "properties": {"query": {**_S, "description": "place name, area id, or 'lat, lon'"}}, "required": ["query"]}},
    {"name": "top_areas", "description": "The highest-risk areas in California right now, highest first.",
     "parameters": {"type": "object", "properties": {"n": {**_I, "description": "how many (1-15), default 5"}}}},
    {"name": "compare_areas", "description": "Compare two areas factor by factor (points each factor contributes) to explain why one scores higher.",
     "parameters": {"type": "object", "properties": {"a": _S, "b": _S}, "required": ["a", "b"]}},
    {"name": "fires_near", "description": "Satellite fire detections near a place in the current window: count, nearest detection, latest time, which satellites saw them.",
     "parameters": {"type": "object", "properties": {"query": _S, "radius_km": {**_N, "description": "default 50"}}, "required": ["query"]}},
    {"name": "satellite_passes", "description": "When the fire-detecting satellites (NOAA-20, NOAA-21, Suomi NPP, Aqua, Terra) next pass over a place, computed from orbital elements with SGP4.",
     "parameters": {"type": "object", "properties": {"query": _S, "hours": {**_N, "description": "look-ahead window, default 12"}}, "required": ["query"]}},
    {"name": "statewide_summary", "description": "Statewide overview: forecast window, number of areas per risk category, top areas, fire detection count, data sources, model description.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "validation", "description": "How well PyroWatch's earlier prediction matched where new fires actually started (hit rate in top 20%, lift over random, ROC AUC).",
     "parameters": {"type": "object", "properties": {"as_of": {**_S, "description": "prediction date YYYY-MM-DD; default two days ago"}}}},
    {"name": "ground_stations", "description": "Latest temperature/humidity readings from PyroWatch Beacon ground stations.",
     "parameters": {"type": "object", "properties": {}}},
]


class Tools:
    def __init__(self, as_of: date | None):
        self.as_of = as_of
        self.payload = risk.compute_risk(as_of)
        self.cells = {c["id"]: c for c in self.payload["cells"]}
        self._fires = None
        self.point = None
        self.touched: list[str] = []  # area ids the agent looked at, for the UI to highlight

    # ---------------------------------------------------------- helpers
    @property
    def fires(self):
        if self._fires is None:
            self._fires = risk.fires(self.as_of, days=3 if self.as_of else 2)
        return self._fires

    def resolve(self, query):
        """-> (area, error). Also sets self.point = (lat, lon, label) of the exact place asked about."""
        q = str(query or "").strip()
        if q in self.cells:
            c = self.cells[q]
            self.point = (c["lat"], c["lon"], c["name"])
            return c, None
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", q)
        if m:
            lat, lon = float(m[1]), float(m[2])
            label = q
        else:
            ql = q.lower()
            place = next((p for p in PLACES if p[0].lower() == ql), None) or next((p for p in PLACES if ql and ql in p[0].lower()), None)
            if place:
                label, lat, lon = place
            else:
                cell = next((c for c in self.cells.values() if ql and ql in c["name"].lower()), None)
                if cell:
                    self.point = (cell["lat"], cell["lon"], cell["name"])
                    return cell, None
                return None, f"Unknown place '{q}'. Try a California town such as {', '.join(p[0] for p in PLACES[::12])}, or 'lat, lon'."
        cid = cell_for_point(lat, lon)
        if cid is None:  # just outside the grid (coast/border): nearest area within 60 km
            near = min(self.cells.values(), key=lambda c: haversine_km(lat, lon, c["lat"], c["lon"]))
            if haversine_km(lat, lon, near["lat"], near["lon"]) > 60:
                return None, f"'{label}' is outside PyroWatch's California coverage."
            cid = near["id"]
        self.point = (lat, lon, label)
        return self.cells[cid], None

    def _area(self, c, detail=True):
        self.touched.append(c["id"])
        out = {"area_id": c["id"], "area": c["name"], "lat": c["lat"], "lon": c["lon"],
               "risk_index": c["risk"], "category": c["category"], "rank": f"{c['rank']} of {len(self.cells)}"}
        if detail:
            out["factors"] = [{"factor": f["label"], "points": f["points"], "measured": f["explain"]}
                              for f in sorted(c["factors"], key=lambda f: -f["points"])]
            out["fuel_gate"] = c["fuel_gate"]
            if c.get("ml_prob") is not None:
                out["ml_7day_new_fire_probability"] = c["ml_prob"]
            out["fire_detections_in_area"] = sum(1 for d in self.fires["detections"] if d.get("cell_id") == c["id"])
            st = ground.list_stations(max_age_hours=3, cell_id=c["id"])
            if st:
                out["ground_stations"] = st
        return out

    def _meta(self):
        modes = {k: v["mode"] for k, v in self.payload["sources"].items()}
        return {"as_of": self.payload["as_of"], "window": self.payload["window"],
                "synthetic_sources": [k for k, v in modes.items() if v != "live"]}

    # ---------------------------------------------------------- tools
    def find_area(self, query):
        c, err = self.resolve(query)
        return {"error": err} if err else {**self._area(c), **self._meta()}

    def top_areas(self, n=5):
        n = max(1, min(int(n or 5), 15))
        ranked = sorted(self.cells.values(), key=lambda c: -c["risk"])[:n]
        return {"areas": [self._area(c, detail=False) for c in ranked], **self._meta()}

    def compare_areas(self, a, b):
        ca, ea = self.resolve(a)
        cb, eb = self.resolve(b)
        if ea or eb:
            return {"error": ea or eb}
        pa = {f["label"]: f["points"] for f in ca["factors"]}
        pb = {f["label"]: f["points"] for f in cb["factors"]}
        diff = sorted(({"factor": k, "a_points": pa[k], "b_points": pb[k], "difference": round(pa[k] - pb[k], 1)} for k in pa),
                      key=lambda x: -abs(x["difference"]))
        return {"a": self._area(ca), "b": self._area(cb), "factor_differences": diff,
                "total_difference": round(ca["risk"] - cb["risk"], 1)}

    def fires_near(self, query, radius_km=50):
        c, err = self.resolve(query)
        if err:
            return {"error": err}
        radius_km = max(5.0, min(float(radius_km or 50), 300.0))
        lat, lon, label = self.point
        near = sorted(((haversine_km(lat, lon, d["lat"], d["lon"]), d) for d in self.fires["detections"]), key=lambda x: x[0])
        near = [(k, d) for k, d in near if k <= radius_km]
        self.touched.append(c["id"])
        return {
            "place": label, "area": c["name"], "radius_km": radius_km, "window_days": self.fires["days"], "count": len(near),
            "nearest": {"km": round(near[0][0], 1), **{k: near[0][1][k] for k in ("time_utc", "satellite", "instrument", "frp", "confidence")}} if near else None,
            "latest_utc": max((d["time_utc"] for _, d in near), default=None),
            "satellites": sorted({d["satellite"] for _, d in near}),
            "data": self.fires["source"]["mode"],
        }

    def satellite_passes(self, query, hours=12):
        c, err = self.resolve(query)
        if err:
            return {"error": err}
        self.touched.append(c["id"])
        lat, lon, label = self.point
        passes, status = orbits.next_passes(lat, lon, hours=max(1.0, min(float(hours or 12), 24.0)))
        return {"place": label, "area": c["name"], "passes": passes, "orbit_data": status["mode"]}

    def statewide_summary(self):
        p = self.payload
        return {**self._meta(), "category_counts": p["counts"], "top": p["top"][:5],
                "fire_detections_in_window": self.fires["count"],
                "model": "weighted index: " + ", ".join(f"{f['label']} {f['weight']:.0%}" for f in p["model"]["factors"]),
                "ml_model": p["model"].get("ml")}

    def validation(self, as_of=None):
        d = date.fromisoformat(as_of) if as_of else self.as_of
        v = risk.validate(d)
        keys = ("as_of", "fire_window", "method", "significant_rule", "new_fire_cells", "hits", "hit_rate", "lift", "auc",
                "all_hotspots", "caveats")
        return {k: v[k] for k in keys} | {
            "significant_new_fire_areas": [{k: c[k] for k in ("name", "rank", "risk", "first_detection_utc", "detections")}
                                           for c in v["cells"] if c["significant"]][:8]}

    def ground_stations(self):
        return {"stations": ground.list_stations()}

    def call(self, name, args):
        if name not in {d["name"] for d in DECLARATIONS}:
            return {"error": f"unknown tool {name}"}
        return getattr(self, name)(**(args or {}))


def _short(args):
    return ", ".join(f"{v}" for v in (args or {}).values())[:60]


def run(question: str, as_of: date | None = None, cell_id: str | None = None):
    question = (question or "").strip()[:600]
    if not question:
        return {"engine": "none", "text": "Ask a question about wildfire risk in California.", "steps": [], "areas": []}
    tools = Tools(as_of)
    if not GEMINI_API_KEY:
        return _offline(tools, question, cell_id)

    note = ""
    if cell_id and cell_id in tools.cells:
        note = f"\n\n[The user has area {cell_id} ({tools.cells[cell_id]['name']}) selected on the map.]"
    if as_of:
        note += f"\n[The map is in REPLAY mode for {as_of.isoformat()}; data reflects that date.]"
    contents = [{"role": "user", "parts": [{"text": question + note}]}]
    steps = []
    try:
        for _ in range(MAX_STEPS):
            resp = ai.gemini_request(ai.SYSTEM + AGENT_RULES, contents, tools=[{"functionDeclarations": DECLARATIONS}], temperature=0.3)
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                return {"engine": GEMINI_MODEL, "text": ai.response_text(resp) or "(no answer)", "steps": steps, "areas": _uniq(tools.touched)}
            contents.append({"role": "model", "parts": parts})  # echo verbatim (keeps thought signatures)
            replies = []
            for call in calls:
                name, args = call.get("name"), call.get("args") or {}
                try:
                    result = tools.call(name, args)
                except Exception as exc:  # noqa: BLE001 - report tool errors back to the model
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                steps.append({"tool": name, "args": _short(args), "ok": "error" not in result})
                fr = {"name": name, "response": {"result": result}}
                if call.get("id"):
                    fr["id"] = call["id"]
                replies.append({"functionResponse": fr})
            contents.append({"role": "user", "parts": replies})
        return {"engine": GEMINI_MODEL, "text": "I needed too many lookups for that one; try a narrower question.", "steps": steps, "areas": _uniq(tools.touched)}
    except Exception as exc:  # noqa: BLE001
        log.warning("agent failed: %s", exc)
        return {"engine": "error", "text": f"Gemini request failed ({type(exc).__name__}). Try again in a moment.", "steps": steps, "areas": _uniq(tools.touched)}


def _uniq(ids):
    return list(dict.fromkeys(ids))


def _offline(tools: Tools, q: str, cell_id: str | None):
    """No Gemini key: route the question to one tool by keywords and format the result."""
    ql = q.lower()
    steps = []

    def use(name, **args):
        steps.append({"tool": name, "args": _short(args), "ok": True})
        return tools.call(name, args)

    place = next((p[0] for p in PLACES if p[0].lower() in ql), None)
    target = place or cell_id
    if any(w in ql for w in ("satellite", "pass", "overhead", "orbit")) and target:
        r = use("satellite_passes", query=target)
        lines = [f"Next fire-watch satellite passes over {r['place']}:"] + [
            f"• {p['satellite']} ({p['instrument']}) in {p['minutes_from_now']} min, {p['closest_km']} km off-track" for p in r["passes"][:4]]
    elif any(w in ql for w in ("highest", "worst", "top", "most", "where")) or not target:
        r = use("top_areas", n=5)
        lines = ["Highest-risk areas right now:"] + [f"• {a['area']}: {a['risk_index']:.0f} ({a['category']})" for a in r["areas"]]
    else:
        r = use("find_area", query=target)
        if "error" in r:
            lines = [r["error"]]
        else:
            top = r["factors"][:3]
            lines = [f"{r['area']}: risk {r['risk_index']:.0f}/100 ({r['category']}), rank {r['rank']}.",
                     *[f"• {f['factor']} +{f['points']}: {f['measured']}" for f in top]]
    lines.append("(Offline answer: add GEMINI_API_KEY for full natural-language answers.)")
    return {"engine": "offline router", "text": "\n".join(lines), "steps": steps, "areas": _uniq(tools.touched)}
