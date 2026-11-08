import unittest
from datetime import date, timedelta

from backend.demo import hourly_weather, weather_series
from backend.ai import _cell_context, _template_briefing
from backend.risk import raw_features, score_severe_weather


class SevereWeatherScoreTests(unittest.TestCase):
    def test_cape_generates_explainable_thunderstorm_score(self):
        scores = score_severe_weather({"cape_max_jkg": 1250, "snowfall_48h_cm": 0, "gust_kmh": 25, "tmax_c": 5})
        storm = scores["thunderstorm"]
        self.assertEqual(storm["score"], 50.0)
        self.assertEqual(storm["factors"][0]["key"], "cape")
        self.assertIn("1250 J/kg", storm["factors"][0]["explain"])

    def test_snowfall_wind_and_cold_contribute_to_blizzard_index(self):
        scores = score_severe_weather({"cape_max_jkg": 0, "snowfall_48h_cm": 25, "gust_kmh": 80, "tmax_c": -15})
        self.assertEqual(scores["blizzard"]["score"], 100.0)
        self.assertEqual([factor["key"] for factor in scores["blizzard"]["factors"]], ["snowfall", "wind", "cold"])

    def test_missing_hourly_metrics_are_not_misreported_as_zero(self):
        scores = score_severe_weather({})
        self.assertIsNone(scores["thunderstorm"]["score"])
        self.assertIsNone(scores["blizzard"]["score"])

    def test_blizzard_score_is_snowfall_gated_and_breakdown_adds_up(self):
        dry = score_severe_weather({"snowfall_48h_cm": 0, "gust_kmh": 80, "tmax_c": -15})
        self.assertEqual(dry["blizzard"]["score"], 0)
        snowy = score_severe_weather({"snowfall_48h_cm": 7.5, "gust_kmh": 80, "tmax_c": -15})
        self.assertEqual(sum(factor["points"] for factor in snowy["blizzard"]["factors"]), snowy["blizzard"]["score"])

    def test_raw_features_aggregate_hourly_derived_daily_metrics_across_48_hours(self):
        as_of = date(2025, 1, 2)
        series = {
            as_of.isoformat(): {"cape_max_jkg": 400, "snowfall_sum_cm": 1.5},
            (as_of + timedelta(days=1)).isoformat(): {"cape_max_jkg": 1100, "snowfall_sum_cm": 2.25},
        }
        features = raw_features(series, as_of, 0, 0)
        self.assertEqual(features["cape_max_jkg"], 1100)
        self.assertEqual(features["snowfall_48h_cm"], 3.75)

    def test_demo_weather_derives_scores_from_deterministic_hourly_metrics(self):
        day = date(2025, 1, 2)
        hourly = hourly_weather(37.5, -119.3, day)
        daily = weather_series([(37.5, -119.3)], day, day)[0][day.isoformat()]
        self.assertEqual(len(hourly), 24)
        self.assertTrue(all("cape" in hour and "snowfall" in hour for hour in hourly))
        self.assertEqual(daily["cape_max_jkg"], max(hour["cape"] for hour in hourly))
        self.assertAlmostEqual(daily["snowfall_sum_cm"], sum(hour["snowfall"] for hour in hourly))

    def test_briefing_context_and_template_follow_selected_hazard(self):
        cell = {
            "id": "r01c01", "name": "Test area", "lat": 37.0, "lon": -120.0,
            "risk": 20, "category": "Moderate", "rank": 1, "factors": [],
            "hazards": {
                "wildfire": {"score": 20, "category": "Moderate", "factors": []},
                "thunderstorm": {"score": 55, "category": "High", "factors": [
                    {"label": "Peak CAPE", "points": 55, "value": 0.55, "explain": "peak hourly CAPE 1375 J/kg"},
                ]},
                "blizzard": {"score": 42, "category": "High", "factors": []},
            },
            "raw": {"ffwi": None}, "fuel_gate": 0.5, "veg_gate": 1, "vegetation": None,
        }
        payload = {
            "cells": [cell], "as_of": "2025-01-02", "window": "48 h",
            "top": [], "sources": {"weather": {"mode": "demo"}},
        }
        context = _cell_context(cell, payload, hazard="thunderstorm")
        text = _template_briefing(context)
        self.assertEqual(context["hazard_score"], 55)
        self.assertIn("THUNDERSTORM", text)
        self.assertIn("CAPE describes atmospheric instability", text)
        self.assertIn("simulated demo data", text)


if __name__ == "__main__":
    unittest.main()
