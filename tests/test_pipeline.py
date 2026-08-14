from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import csv
import json
import math
import tempfile
import unittest
from unittest.mock import patch

from armor_impact import predict_injury
from armor_impact.api import _resolve_config_path, normalize_armor_type
from armor_impact.config import SolverConfig, load_config
from armor_impact.deck import build_case
from armor_impact.postprocess import (
    INJURY_INPUT_FILENAME,
    INJURY_INPUTS_FILENAME,
    analyze_case,
    analyze_study,
    _validate_injury_prediction_input,
    parse_glstat,
    parse_lsdyna_float,
    parse_nodout,
)
from armor_impact.runner import RunResult, inspect_case, resolve_executable, solver_command


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def write_fixture_case(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodout").write_bytes((FIXTURES / "sample_nodout").read_bytes())
    (root / "glstat").write_bytes((FIXTURES / "sample_glstat").read_bytes())
    metadata = {
        "case_id": "fixture",
        "case": {
            "armor_type": "plate", "caliber_mm": 80.0, "speed_mps": 100.0,
            "yaw_deg": 0.0, "pitch_deg": 0.0, "impact_x_mm": 0.0,
            "impact_z_mm": 0.0, "mesh_scale": 1.0,
        },
        "projectile_mass_kg": 2.0,
        "projectile_material": {
            "density_kg_m3": 7200.0,
            "youngs_modulus_gpa": 120.0,
            "poisson": 0.25,
            "standoff_mm": 40.0,
        },
        "body_depth_mm": 200.0,
        "sensors": {
            "impact_front": 1, "impact_back": 2,
            "chest_front": 1, "chest_back": 2,
            "abdomen_front": 1, "abdomen_back": 2,
            "projectile_center": 3, "armor_near_impact": 4, "torso_center": 5,
        },
        "history_elements": {
            "body_near_impact": 101,
            "body_near_chest": 102,
            "body_near_abdomen": 103,
            "armor_near_impact": 201,
            "projectile": 301,
        },
        "model_limitations": [],
    }
    (root / "case.json").write_text(json.dumps(metadata), encoding="utf-8")


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
            self.assertIn("body_near_chest", metadata["history_elements"])
            self.assertIn("body_near_abdomen", metadata["history_elements"])

    def test_build_case_uses_requested_projectile_mass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = replace(self.config.cases[0], projectile_mass_kg=3.25)
            metadata = build_case(self.config, case, Path(tmp))
            self.assertEqual(metadata["projectile_mass_kg"], 3.25)
            self.assertGreater(metadata["projectile_mass_scale"], 1.0)
            self.assertGreater(metadata["projectile_effective_density_kg_m3"], 7200.0)
            self.assertIn("_w3p25", metadata["case_id"])

    def test_build_case_without_armor_omits_armor_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = replace(
                self.config.cases[0],
                armor_type="none",
                projectile_mass_kg=3.25,
            )
            metadata = build_case(self.config, case, Path(tmp))
            deck = (Path(tmp) / "run.k").read_text(encoding="utf-8")
            self.assertNotIn("*SECTION_SHELL\n", deck)
            self.assertNotIn("*ELEMENT_SHELL\n", deck)
            self.assertNotIn("Armor - none", deck)
            self.assertNotIn("armor_near_impact", metadata["sensors"])
            self.assertIsNone(metadata["armor_material"])


class PublicApiTests(unittest.TestCase):
    def test_project_study_config_is_used_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(tmp) / "study.toml"
            expected.write_text("", encoding="utf-8")
            with patch("armor_impact.api.Path.cwd", return_value=Path(tmp)):
                self.assertEqual(_resolve_config_path(None), expected)

    def test_armor_names_are_normalized(self) -> None:
        self.assertEqual(normalize_armor_type("두정갑"), "dujeong_equivalent")
        self.assertEqual(normalize_armor_type("플레이트"), "plate")
        self.assertEqual(normalize_armor_type("없음"), "none")

    def test_predict_injury_runs_one_case_and_returns_dictionary(self) -> None:
        payload = {
            "schema_version": "injury-prediction-input/v3",
            "case_id": "test-case",
            "injury_prediction_ready": True,
        }
        run_result = RunResult("test-case", "completed", 0, 0.1, "completed")
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("armor_impact.api.run_case", return_value=run_result),
                patch(
                    "armor_impact.api.analyze_case",
                    return_value={"injury_prediction_input": payload},
                ),
            ):
                result = predict_injury(
                    "두정갑",
                    250.0,
                    80.0,
                    3.8,
                    output_dir=tmp,
                )
            self.assertIs(result, payload)
            case_files = list(Path(tmp).glob("*/case.json"))
            self.assertEqual(len(case_files), 1)
            metadata = json.loads(case_files[0].read_text(encoding="utf-8"))
            self.assertEqual(metadata["case"]["armor_type"], "dujeong_equivalent")
            self.assertEqual(metadata["projectile_mass_kg"], 3.8)
            self.assertEqual(metadata["run_id"], case_files[0].parent.name)
            self.assertGreater(metadata["projectile_mass_scale"], 1.0)

    def test_predict_injury_rejects_unknown_armor(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported armor_type"):
            predict_injury("chainmail", 250.0, 80.0, 3.8)


class ParserAndMetricTests(unittest.TestCase):
    def test_fortran_float_without_e(self) -> None:
        self.assertAlmostEqual(parse_lsdyna_float("1.25000-3"), 0.00125)

    def test_parse_nodout(self) -> None:
        frames = parse_nodout(FIXTURES / "sample_nodout")
        self.assertEqual(len(frames), 3)
        self.assertAlmostEqual(frames[-1].nodes[1].displacement_mm[1], 15.0)
        self.assertAlmostEqual(frames[-1].nodes[3].velocity_mps[1], 50.0)

    def test_parse_nodout_with_adjacent_negative_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nodout"
            path.write_text(
                "n o d a l ( at time 0.0000000E+00 )\n"
                " 2263 0.00000E+00 0.00000E+00 0.00000E+00 "
                "0.00000E+00 0.00000E+00 0.00000E+00 "
                "0.00000E+00 0.00000E+00 0.00000E+00 "
                "0.00000E+00-1.00000E+02-9.37500E+01\n",
                encoding="utf-8",
            )
            frames = parse_nodout(path)
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].nodes[2263].coordinate_mm, (0.0, -100.0, -93.75))

    def test_parse_glstat(self) -> None:
        frames = parse_glstat(FIXTURES / "sample_glstat")
        self.assertEqual(len(frames), 2)
        self.assertAlmostEqual(frames[-1].values["energy_ratio"], 1.0)
        self.assertAlmostEqual(frames[-1].values["hourglass_energy_j"], 10.0)

    def test_parse_glstat_with_dotted_r14_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glstat"
            path.write_text(
                " time...........................   0.00000E+00\n"
                " time step......................   2.42398E-03\n"
                " internal energy................   3.90000E+02\n"
                " hourglass energy ..............   1.00000E+01\n"
                " eroded internal energy.........   4.00000E+01\n"
                " eroded hourglass energy........   2.00000E+00\n"
                " total energy / initial energy..   1.00000E+00\n",
                encoding="utf-8",
            )
            frames = parse_glstat(path)
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].values["internal_energy_j"], 390.0)
            self.assertEqual(frames[0].values["hourglass_energy_j"], 10.0)
            self.assertEqual(frames[0].values["eroded_internal_energy_j"], 40.0)
            self.assertEqual(frames[0].values["eroded_hourglass_energy_j"], 2.0)
            self.assertEqual(frames[0].values["energy_ratio"], 1.0)

    def test_analyze_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_case(root)
            result = analyze_case(root)
            impact = result["impact_site"]
            self.assertAlmostEqual(impact["max_deflection_mm"], 10.0)
            self.assertAlmostEqual(impact["max_compression_ratio"], 0.05)
            self.assertAlmostEqual(result["projectile_residual_speed_mps"], 50.0)
            self.assertAlmostEqual(result["torso_center_peak_acceleration_g"], 2.0, places=5)
            self.assertTrue(math.isclose(result["final_energy_ratio"], 1.0))
            injury_input = result["injury_prediction_input"]
            self.assertTrue(injury_input["injury_prediction_ready"])
            self.assertEqual(injury_input["schema_version"], "injury-prediction-input/v3")
            self.assertEqual(injury_input["prediction_result"]["status"], "not_scored")
            self.assertNotIn("units", injury_input)
            self.assertTrue(
                injury_input["model_context"]["unit_convention"][
                    "field_units_encoded_in_names"
                ]
            )
            self.assertEqual(
                injury_input["model_context"]["model_type"],
                "homogeneous_viscoelastic_torso_surrogate",
            )
            self.assertAlmostEqual(
                injury_input["projectile_response"][
                    "projectile_kinetic_energy_loss_fraction"
                ],
                0.75,
            )
            self.assertNotIn("projectile_energy_change_j", injury_input["projectile_response"])
            self.assertEqual(
                injury_input["torso_response"]["impact_site"]["measurement_basis"],
                "paired_single_front_and_back_surface_nodes",
            )
            self.assertIn(
                "torso_center_acceleration",
                injury_input["torso_response"],
            )
            self.assertAlmostEqual(
                injury_input["torso_response"]["impact_site"]["max_deflection_mm"], 10.0
            )
            self.assertTrue((root / INJURY_INPUT_FILENAME).is_file())

    def test_analyze_case_without_armor_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_case(root)
            metadata = json.loads((root / "case.json").read_text(encoding="utf-8"))
            metadata["case"]["armor_type"] = "none"
            metadata["sensors"].pop("armor_near_impact")
            (root / "case.json").write_text(json.dumps(metadata), encoding="utf-8")

            result = analyze_case(root)
            self.assertIsNone(result["armor_peak_ap_displacement_mm"])
            self.assertIsNone(result["armor_local_failure_detected"])
            self.assertEqual(
                result["injury_prediction_input"]["impact_conditions"]["armor_type"],
                "none",
            )
            armor = result["injury_prediction_input"]["armor_response"]
            self.assertIsNone(armor["armor_perforation_detected"])
            self.assertEqual(armor["armor_perforation_status"], "not_applicable_no_armor")

    def test_analyze_case_limits_armor_displacement_to_node_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_case(root)
            (root / "solver.log").write_text(
                "node number 4 deleted at time 1.5000E+00\n",
                encoding="utf-8",
            )

            result = analyze_case(root)
            self.assertEqual(result["armor_peak_ap_displacement_mm"], 7.0)
            self.assertTrue(result["armor_local_failure_detected"])
            self.assertEqual(result["armor_sensor_deletion_time_ms"], 1.5)
            self.assertEqual(result["armor_displacement_history_scope"], "pre_local_failure")

    def test_projectile_element_failure_invalidates_residual_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_case(root)
            (root / "solver.log").write_text(
                "solid element 301 failed at time 1.5000E+00\n",
                encoding="utf-8",
            )

            result = analyze_case(root)
            payload = result["injury_prediction_input"]
            self.assertIsNone(payload["projectile_response"]["projectile_residual_speed_mps"])
            self.assertEqual(
                payload["projectile_response"]["residual_measurement"]["status"],
                "invalid_tracked_projectile_element_failed",
            )
            self.assertFalse(payload["injury_prediction_ready"])

    def test_validation_rejects_nonfinite_required_feature(self) -> None:
        payload = {
            "prediction_result": {
                "status": "not_scored",
                "injury_probability": None,
                "injury_severity": None,
            },
            "impact_conditions": {
                "projectile_mass_kg": 2.0,
                "projectile_mass_scale": 1.0,
                "impact_speed_mps": 100.0,
                "projectile_initial_ke_j": 10000.0,
            },
            "projectile_response": {
                "projectile_residual_speed_mps": 50.0,
                "projectile_residual_ke_j": 2500.0,
                "projectile_kinetic_energy_loss_j": 7500.0,
            },
            "armor_response": {
                "armor_perforation_detected": None,
                "displacement_history_scope": "full_parsed_nodout_history",
            },
            "torso_response": {
                "impact_site": {
                    "max_deflection_mm": float("nan"),
                    "max_compression_ratio": 0.05,
                    "peak_vc_mps": 0.2,
                },
                "chest": {},
                "abdomen": {},
                "torso_center_acceleration": {},
            },
            "simulation_quality": {
                "simulation_duration_ms": 5.0,
                "warnings": [],
            },
            "injury_prediction_ready": True,
        }
        validated = _validate_injury_prediction_input(payload)
        self.assertFalse(validated["injury_prediction_ready"])
        self.assertIsNone(
            validated["torso_response"]["impact_site"]["max_deflection_mm"]
        )
        self.assertEqual(validated["simulation_quality"]["validation_status"], "failed")

    def test_validation_rejects_not_scored_prediction_with_result(self) -> None:
        payload = {
            "prediction_result": {
                "status": "not_scored",
                "injury_probability": 0.8,
                "injury_severity": None,
            },
            "impact_conditions": {
                "projectile_mass_kg": 2.0,
                "projectile_mass_scale": 1.0,
                "impact_speed_mps": 100.0,
                "projectile_initial_ke_j": 10000.0,
            },
            "projectile_response": {
                "projectile_residual_speed_mps": 50.0,
                "projectile_residual_ke_j": 2500.0,
                "projectile_kinetic_energy_loss_j": 7500.0,
            },
            "armor_response": {"armor_perforation_detected": None},
            "torso_response": {
                "impact_site": {
                    "max_deflection_mm": 10.0,
                    "max_compression_ratio": 0.05,
                    "peak_vc_mps": 0.2,
                },
                "torso_center_acceleration": {},
            },
            "simulation_quality": {"simulation_duration_ms": 5.0, "warnings": []},
            "injury_prediction_ready": True,
        }
        validated = _validate_injury_prediction_input(payload)
        self.assertFalse(validated["injury_prediction_ready"])
        self.assertIn(
            "prediction_result is not_scored",
            " ".join(validated["simulation_quality"]["validation_errors"]),
        )

    def test_validation_rejects_measurement_after_simulation(self) -> None:
        payload = {
            "prediction_result": {
                "status": "not_scored",
                "injury_probability": None,
                "injury_severity": None,
            },
            "impact_conditions": {
                "projectile_mass_kg": 2.0,
                "projectile_mass_scale": 1.0,
                "impact_speed_mps": 100.0,
                "projectile_initial_ke_j": 10000.0,
            },
            "projectile_response": {
                "projectile_residual_speed_mps": 50.0,
                "projectile_residual_ke_j": 2500.0,
                "projectile_kinetic_energy_loss_j": 7500.0,
            },
            "armor_response": {"armor_perforation_detected": None},
            "torso_response": {
                "impact_site": {
                    "max_deflection_mm": 10.0,
                    "max_compression_ratio": 0.05,
                    "peak_vc_mps": 0.2,
                    "time_of_peak_vc_ms": 6.0,
                },
                "torso_center_acceleration": {},
            },
            "simulation_quality": {"simulation_duration_ms": 5.0, "warnings": []},
            "injury_prediction_ready": True,
        }
        validated = _validate_injury_prediction_input(payload)
        self.assertFalse(validated["injury_prediction_ready"])
        self.assertIn(
            "exceeds simulation duration",
            " ".join(validated["simulation_quality"]["validation_errors"]),
        )

    def test_analyze_study_writes_batch_injury_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture_case(root / "fixture")
            with (root / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["case_id"])
                writer.writeheader()
                writer.writerow({"case_id": "fixture"})

            summary = analyze_study(root)
            self.assertTrue(summary.is_file())
            lines = (root / INJURY_INPUTS_FILENAME).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["case_id"], "fixture")
            self.assertTrue(payload["injury_prediction_ready"])


class RunnerInspectionTests(unittest.TestCase):
    def test_resolve_executable_reads_project_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "ls-dyna_smp_d.exe"
            executable.write_bytes(b"")
            (root / ".env").write_text(
                f'LS_DYNA_EXECUTABLE="{executable}"\n',
                encoding="utf-8",
            )
            with (
                patch.dict("os.environ", {"LS_DYNA_EXECUTABLE": ""}),
                patch("armor_impact.runner.Path.cwd", return_value=root),
            ):
                self.assertEqual(
                    resolve_executable(r"C:\ignored\lsdyna.exe"),
                    executable.resolve(),
                )

    def test_memory_mb_is_converted_to_double_precision_mwords(self) -> None:
        solver = SolverConfig("", 2, 2048, 120.0)
        command = solver_command(Path("ls-dyna_smp_d.exe"), Path("case"), solver)
        self.assertEqual(command[-1], "memory=256m")

    def test_memory_mb_is_converted_to_single_precision_mwords(self) -> None:
        solver = SolverConfig("", 2, 2048, 120.0)
        command = solver_command(Path("ls-dyna_smp_s.exe"), Path("case"), solver)
        self.assertEqual(command[-1], "memory=512m")

    def test_normal_termination_is_not_failed_by_option_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "solver.log").write_text(
                "[license/error] *** ANSYS LICENSE MANAGER ERROR ***\n"
                " Student license active; continuing.\n",
                encoding="utf-8",
            )
            (root / "d3hsp").write_text(
                "          eq.2:  error termination if too small\n"
                " N o r m a l    t e r m i n a t i o n\n",
                encoding="utf-8",
            )
            (root / "nodout").write_text("history output\n", encoding="utf-8")
            self.assertEqual(
                inspect_case(root),
                ("completed", "Normal termination and result output detected"),
            )

    def test_fatal_marker_fails_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "solver.log").write_text("*** Error 10117 (KEY+117)\n", encoding="utf-8")
            self.assertEqual(
                inspect_case(root),
                ("failed", "LS-DYNA Error 10117 (KEY+117)"),
            )

    def test_fatal_marker_includes_solver_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "solver.log").write_text(
                "*** Error 70023 (OTH+23)\n"
                "LS-DYNA failed to allocate the requested memory.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                inspect_case(root),
                (
                    "failed",
                    "LS-DYNA Error 70023 (OTH+23): "
                    "LS-DYNA failed to allocate the requested memory.",
                ),
            )


if __name__ == "__main__":
    unittest.main()
