"""Streamlit 위젯 전환·실행·키 설정과 HTML 인코딩 회귀 검사."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=45).run()
        self.assertFalse(app.exception)
        return app

    def assertHealthy(self, app):
        self.assertFalse(app.exception, [error.message for error in app.exception])

    def test_osm_component_uses_stable_identity_and_structured_payload(self):
        from unittest.mock import patch, Mock
        from dashboard.app import render_openstreetmap_map
        payload = {"vehicle_points": [{"coordinates": [127, 37.5], "label": "</script>"}],
                   "vehicle_start_points": [], "request_origin_points": [], "request_destination_points": []}
        component = Mock()
        with patch("dashboard.app.build_map_payload", return_value=payload), patch("dashboard.app.build_coordinate_svg", return_value="<svg></svg>"), patch("dashboard.app.components.declare_component", return_value=component):
            render_openstreetmap_map({}, compact=True, show_supporting_info=False)
            first = component.call_args.kwargs
            render_openstreetmap_map({}, compact=True, show_supporting_info=False)
            self.assertEqual(first["key"], component.call_args.kwargs["key"])
            self.assertEqual(first["height"], 540)
            self.assertEqual(first["payload"]["vehicle_points"][0]["label"], "</script>")
            render_openstreetmap_map({}, show_supporting_info=False)
            self.assertNotEqual(first["key"], component.call_args.kwargs["key"])
        html = (ROOT / "dashboard/leaflet_map/index.html").read_text(encoding="utf-8")
        self.assertIn("streamlit:render", html)
        self.assertIn("if (!renderer)", html)
        self.assertIn("showFallback", html)
        self.assertNotIn("const payload =", html)

    def test_default_simple_mode_and_no_key(self):
        app = self.app()
        self.assertEqual([control.label for control in app.number_input], ["차량 수", "요청 수", "실행 시간 (초)"])
        self.assertEqual(app.session_state["dashboard_editor_snapshot"]["config"]["vehicles"]["initial_positions"], [[37.513620, 126.941570]])
        self.assertFalse(any("Key" in control.label for control in app.text_input))
        self.assertFalse(any(control.label == "지도 표시 방식" for control in app.selectbox))

    def test_mode_switch_preserves_counts_time_and_positions(self):
        app = self.app()
        app.number_input(key="quick_vehicle_count").set_value(3).run()
        app.number_input(key="quick_request_count").set_value(5).run()
        app.number_input(key="quick_duration").set_value(3600).run()
        requests = app.session_state["dashboard_editor_snapshot"]["scenario"]["requests"]
        app.toggle[0].set_value(True).run()
        self.assertHealthy(app)
        self.assertEqual(app.number_input(key="vehicle_count_input").value, 3)
        self.assertEqual(app.number_input(key="request_count_input").value, 5)
        app.number_input(key="vehicle_lat_0").set_value(37.5).run()
        app.toggle[0].set_value(False).run()
        self.assertHealthy(app)
        self.assertEqual(app.number_input(key="quick_vehicle_count").value, 3)
        self.assertEqual(app.number_input(key="quick_duration").value, 3600)
        snapshot = app.session_state["dashboard_editor_snapshot"]
        self.assertEqual(snapshot["config"]["vehicles"]["initial_positions"][0][0], 37.5)
        self.assertEqual(snapshot["scenario"]["requests"], requests)

    def test_regenerate_is_explicit_and_stable_on_rerun(self):
        app = self.app()
        original = app.session_state["dashboard_editor_snapshot"]["scenario"]["requests"]
        app.button(key="quick_regenerate").click().run()
        generated = app.session_state["dashboard_editor_snapshot"]["scenario"]["requests"]
        self.assertNotEqual(original, generated)
        app.run()
        self.assertEqual(app.session_state["dashboard_editor_snapshot"]["scenario"]["requests"], generated)
        self.assertHealthy(app)

    def test_run_then_map_change_does_not_invalidate_local_routing(self):
        app = self.app()
        next(button for button in app.button if button.label == "시뮬레이션 실행").click().run()
        self.assertHealthy(app)
        self.assertIn("dashboard_result", app.session_state)
        app.run()
        self.assertHealthy(app)
        self.assertFalse(any("마지막 실행 결과와 다릅니다" in message.value for message in app.warning))

    def test_playback_keeps_complete_episode_bounds(self):
        app = self.app()
        next(button for button in app.button if button.label == "시뮬레이션 실행").click().run()
        self.assertHealthy(app)
        result = app.session_state["dashboard_result"]
        original_bounds = result["map_view_bounds"]
        self.assertIsNotNone(original_bounds)
        slider = next(control for control in app.slider if control.label == "단계별 결과 보기")
        last_frame = slider.max
        slider.set_value(0).run()
        self.assertHealthy(app)
        self.assertEqual(app.session_state["dashboard_result"]["map_view_bounds"], original_bounds)
        next(control for control in app.slider if control.label == "단계별 결과 보기").set_value(last_frame // 2).run()
        self.assertHealthy(app)
        self.assertEqual(app.session_state["dashboard_result"]["map_view_bounds"], original_bounds)

    def test_bounds_include_detours_future_requests_and_stationary_case(self):
        from dashboard.app import build_map_view_bounds, render_openstreetmap_map
        from unittest.mock import patch
        timeline = [
            {"vehicles": [{"position": [37.51362,126.94157], "active_route_path": [[37.52,126.93],[37.51,126.94]]}], "requests": []},
            {"vehicles": [{"position": [37.51,126.94], "route_history_paths": [[[37.53,126.92],[37.51,126.94]]]}],
             "requests": [{"origin":[37.50,126.95], "destination":[37.49,126.96]}]},
        ]
        bounds = build_map_view_bounds(timeline)
        self.assertLess(bounds[0][0],37.49)
        self.assertLess(bounds[0][1],126.92)
        self.assertGreater(bounds[1][0],37.53)
        self.assertGreater(bounds[1][1],126.96)
        self.assertIsNone(build_map_view_bounds([]))
        stationary = build_map_view_bounds([{"vehicles":[{"position":[37.51362,126.94157]}]}])
        self.assertGreater(stationary[1][0]-stationary[0][0],0)
        payload = {"vehicle_points":[{"coordinates":[126.94,37.51]}], "vehicle_start_points":[], "request_origin_points":[], "request_destination_points":[]}
        with patch("dashboard.app.build_map_payload",return_value=payload), patch("dashboard.app.build_coordinate_svg",return_value="<svg></svg>"), patch("dashboard.app.components.declare_component") as declare:
            render_openstreetmap_map({}, timeline=[], view_bounds=bounds, show_supporting_info=False)
        self.assertEqual(declare.return_value.call_args.kwargs["payload"]["view_bounds"], bounds)

    def test_grid_source_resets_old_settings_and_still_runs(self):
        app = self.app()
        app.number_input(key="quick_vehicle_count").set_value(3).run()
        next(control for control in app.selectbox if control.label == "기본 Config").select(ROOT / "configs/base.yaml")
        next(control for control in app.selectbox if control.label == "기본 Scenario").select(ROOT / "scenarios/scenario_simple_01.yaml")
        app.run()
        self.assertHealthy(app)
        self.assertEqual(app.number_input(key="quick_vehicle_count").value, 1)
        self.assertEqual(app.session_state["dashboard_editor_snapshot"]["config"]["simulation"]["space_mode"], "grid")
        next(button for button in app.button if button.label == "시뮬레이션 실행").click().run()
        self.assertHealthy(app)


if __name__ == "__main__":
    unittest.main()
