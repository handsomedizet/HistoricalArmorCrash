from __future__ import annotations

from pathlib import Path
import json
import math
import tempfile
import unittest

from armor_impact.config import load_config
from armor_impact.deck import build_case
from armor_impact.postprocess import analyze_case, parse_glstat, parse_lsdyna_float, parse_nodout


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ConfigAndDeckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(ROOT / "study.example.toml")

    def test_case_expansion(self) -> None:
        self.assertEqual(len(self.config.cases), 8)
        self.assertEqual(self.config.cases[0].direction, (0.0, 1.0, 0.0))

    def test_build_case_contains_required_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metadata = build_case(self.config, self.config.cases[0], Path(tmp))
            deck = (Path(tmp) / "run.k").read_text(encoding="utf-8")
            self.assertIn("*CONTACT_ERODING_SINGLE_SURFACE\n", deck)
            self.assertIn("*DATABASE_HISTORY_NODE\n", deck)
            self.assertIn("*MAT_VISCOELASTIC\n", deck)
            self.assertIn("*INITIAL_VELOCITY_NODE\n", deck)
            self.assertGreater(deck.count("*ELEMENT_SOLID"), 0)
            self.assertGreater(float(metadata["projectile_mass_kg"]), 0.0)
            self.assertEqual(len(metadata["sensors"]), 9)


class ParserAndMetricTests(unittest.TestCase):
    def test_fortran_float_without_e(self) -> None:
        self.assertAlmostEqual(parse_lsdyna_float("1.25000-3"), 0.00125)

    def test_parse_nodout(self) -> None:
        frames = parse_nodout(FIXTURES / "sample_nodout")
        self.assertEqual(len(frames), 3)
        self.assertAlmostEqual(frames[-1].nodes[1].displacement_mm[1], 15.0)
        self.assertAlmostEqual(frames[-1].nodes[3].velocity_mps[1], 50.0)

    def test_parse_glstat(self) -> None:
        frames = parse_glstat(FIXTURES / "sample_glstat")
        self.assertEqual(len(frames), 2)
        self.assertAlmostEqual(frames[-1].values["energy_ratio"], 1.0)
        self.assertAlmostEqual(frames[-1].values["hourglass_energy_j"], 10.0)

    def test_analyze_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nodout").write_bytes((FIXTURES / "sample_nodout").read_bytes())
            (root / "glstat").write_bytes((FIXTURES / "sample_glstat").read_bytes())
            metadata = {
                "case_id": "fixture",
                "case": {
                    "armor_type": "plate", "caliber_mm": 80.0, "speed_mps": 100.0,
                    "yaw_deg": 0.0, "pitch_deg": 0.0, "mesh_scale": 1.0,
                },
                "projectile_mass_kg": 2.0,
                "body_depth_mm": 200.0,
                "sensors": {
                    "impact_front": 1, "impact_back": 2,
                    "chest_front": 1, "chest_back": 2,
                    "abdomen_front": 1, "abdomen_back": 2,
                    "projectile_center": 3, "armor_near_impact": 4, "torso_center": 5,
                },
                "model_limitations": [],
            }
            (root / "case.json").write_text(json.dumps(metadata), encoding="utf-8")
            result = analyze_case(root)
            impact = result["impact_site"]
            self.assertAlmostEqual(impact["max_deflection_mm"], 10.0)
            self.assertAlmostEqual(impact["max_compression_ratio"], 0.05)
            self.assertAlmostEqual(result["projectile_residual_speed_mps"], 50.0)
            self.assertAlmostEqual(result["torso_center_peak_acceleration_g"], 2.0, places=5)
            self.assertTrue(math.isclose(result["final_energy_ratio"], 1.0))


if __name__ == "__main__":
    unittest.main()
