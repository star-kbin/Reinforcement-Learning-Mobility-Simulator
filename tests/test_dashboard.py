"""Streamlit 위젯 전환·실행·키 설정과 HTML 인코딩 회귀 검사."""

import json
import re
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
from dashboard.tmap_view import build_tmap_html

ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=45).run()
        self.assertFalse(app.exception)
        return app

    def assertHealthy(self, app):
        self.assertFalse(app.exception, [error.message for error in app.exception])

    def test_script_data_encoding(self):
        key = '</script><img src=x onerror="alert(1)">&한글'
        html = build_tmap_html({"vehicle_points": []}, key, "<svg></svg>")
        self.assertNotIn(key, html)
        encoded = re.search(r'<script id="tmap-data" type="application/json">(.*?)</script>', html, re.S)[1]
        self.assertEqual(json.loads(encoded)["appKey"], key)

    def test_default_simple_mode_and_no_key(self):
        app = self.app()
        self.assertEqual([control.label for control in app.number_input], ["차량 수", "요청 수", "실행 시간 (초)"])
        self.assertEqual(app.selectbox(key="sidebar_map_provider").value, "tmap")
        self.assertTrue(any("현재는 좌표 보기" in message.value for message in app.info))

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
        app.selectbox(key="sidebar_map_provider").select("coordinate").run()
        self.assertHealthy(app)
        self.assertFalse(any("마지막 실행 결과와 다릅니다" in message.value for message in app.warning))

    def test_secrets_key_creates_tmap_component(self):
        app = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=45)
        app.secrets["TMAP_APP_KEY"] = "test-key-not-real"
        app.run()
        self.assertHealthy(app)
        self.assertEqual(app.text_input(key="sidebar_tmap_app_key").value, "test-key-not-real")
        self.assertFalse(any("현재는 좌표 보기" in message.value for message in app.info))

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
