"""Coordinate, model-boundary and no-spend tests. No paid API calls or taps."""

import contextlib
import copy
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

import assistant_step
import model_control as control
import ocr_view
from phone_capture import ProbeError


def view():
    return {"schema_version": 1, "observation_id": "page-123", "width": 900, "height": 1400,
            "roi": [50, 100, 850, 1200], "roi_sha256": "old-hash", "elements": [
                {"id": "t001", "text": "A. 猫", "confidence": 0.99,
                 "bounds": [100, 400, 300, 460], "center": [199, 429]}]}


def decision():
    return {"observation_id": "page-123", "state": "question", "action": "tap",
            "target_id": "t001", "confidence": 0.99, "reason": "选择 cat 的中文含义：猫。"}


class OCRCoordinatesTests(unittest.TestCase):
    def test_roi_maps_back_to_original_screen(self):
        result = ocr_view.normalize_elements([[[10, 20], [110, 20], [110, 60], [10, 60]]],
                    ["A. 猫"], [0.99], [50, 100, 850, 1200], 900, 1400)
        self.assertEqual(result[0]["bounds"], [60, 120, 160, 160])
        self.assertEqual(result[0]["center"], [109, 139])

    def test_zero_negative_and_oversized_roi_stop(self):
        for roi in ([0, 0, 0, 10], [-1, 0, 10, 10], [0, 0, 901, 1400]):
            with self.subTest(roi=roi), self.assertRaises(ProbeError):
                ocr_view.check_roi(roi, 900, 1400)

    def test_outside_text_box_does_not_become_edge_click(self):
        with self.assertRaises(ProbeError):
            ocr_view.normalize_elements([[[1000, 0], [1100, 0], [1100, 60], [1000, 60]]],
                                        ["outside"], [1], [0, 0, 900, 1400], 900, 1400)

    def test_nonfinite_coordinates_stop(self):
        with self.assertRaises(ProbeError):
            ocr_view.normalize_elements([[[float("nan"), 0]] * 4], ["bad"], [1],
                                        [0, 0, 900, 1400], 900, 1400)

    def test_synthetic_ocr_works_without_python_network(self):
        with patch.object(socket.socket, "connect", side_effect=RuntimeError("Network disabled")):
            result = ocr_view.observe(ocr_view.demo_png())
        texts = [item["text"] for item in result["elements"]]
        for expected in ("cat", "猫", "狗", "下一题"):
            self.assertTrue(any(expected in text for text in texts), expected)
        self.assertFalse(result["live_capture"])


class DecisionValidationTests(unittest.TestCase):
    def test_valid_target_uses_local_coordinates_and_requires_confirmation(self):
        proposal = control.validate_decision(view(), decision())
        self.assertEqual((proposal["x"], proposal["y"]), (199, 429))
        self.assertTrue(proposal["requires_confirmation"])

    def test_model_cannot_supply_coordinates_or_shell(self):
        for key, value in (("x", 500), ("shell", "input tap 1 1")):
            invalid = {**decision(), key: value}
            with self.subTest(key=key), self.assertRaises(ProbeError):
                control.validate_decision(view(), invalid)

    def test_stale_observation_unknown_target_and_bad_action_stop(self):
        for key, value in (("observation_id", "old-page"), ("target_id", "t999"),
                           ("action", "shell"), ("action", []), ("state", [])):
            invalid = {**decision(), key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ProbeError):
                control.validate_decision(view(), invalid)

    def test_uncertain_model_or_ocr_cannot_tap(self):
        for value in (0.8, float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ProbeError):
                control.validate_decision(view(), {**decision(), "confidence": value})
        invalid_view = view()
        invalid_view["elements"][0]["confidence"] = 0.7
        with self.assertRaises(ProbeError):
            control.validate_decision(invalid_view, decision())

    def test_out_of_bounds_center_stops(self):
        invalid_view = view()
        invalid_view["elements"][0]["center"] = [9999, 10]
        with self.assertRaises(ProbeError):
            control.validate_decision(invalid_view, decision())

    def test_unknown_page_cannot_tap(self):
        with self.assertRaises(ProbeError):
            control.validate_decision(view(), {**decision(), "state": "unknown"})

    def test_handoff_has_no_tap(self):
        result = control.validate_decision(view(), {**decision(), "state": "unknown",
                            "action": "handoff", "target_id": None})
        self.assertEqual(result["action"], "handoff")
        self.assertNotIn("x", result)


class ModelRequestTests(unittest.TestCase):
    def test_request_is_text_only_and_bounded(self):
        data = control.build_request(view())
        self.assertEqual(data["model"], "deepseek-flash")
        self.assertEqual(data["max_tokens"], 512)
        self.assertEqual(data["thinking"], {"type": "disabled"})
        for message in data["messages"]:
            self.assertIsInstance(message["content"], str)
        self.assertNotIn("image_url", json.dumps(data))
        self.assertNotIn("tools", data)

    def test_empty_or_oversized_ocr_stops_before_request(self):
        for text in (None, "X" * 20000):
            invalid_view = view()
            if text is None:
                invalid_view["elements"] = []
            else:
                invalid_view["elements"][0]["text"] = text
            with self.subTest(text_is_none=text is None), self.assertRaises(ProbeError):
                control.build_request(invalid_view)

    def test_missing_key_does_not_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {}, clear=True), patch.object(control, "ROOT", Path(directory)):
                with self.assertRaises(ProbeError):
                    control.load_api_key()

    def test_local_key_is_loaded_without_printing(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".env.local").write_text("DEEPSEEK_API_KEY=TEST_ONLY_KEY\n", encoding="utf-8")
            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), patch.object(control, "ROOT", Path(directory)):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(control.load_api_key(), "TEST_ONLY_KEY")
            self.assertEqual(output.getvalue(), "")

    def test_redirect_is_not_followed(self):
        with self.assertRaises(ProbeError):
            control.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.example")

    @patch("model_control.urllib.request.build_opener")
    def test_api_adapter_makes_one_request_and_reports_usage(self, build_opener):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(decision())}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}).encode()
        build_opener.return_value.open.return_value = response
        reply, usage = control.call_deepseek(control.build_request(view()), "TEST_ONLY_KEY")
        self.assertEqual(reply, decision())
        self.assertEqual(usage["api_calls"], 1)
        self.assertEqual(usage["estimated_usd_at_2026_10_24_peak_cache_miss_rates"], 0.00042)
        build_opener.return_value.open.assert_called_once()

    @patch("model_control.urllib.request.build_opener")
    def test_timeout_never_retries(self, build_opener):
        build_opener.return_value.open.side_effect = TimeoutError()
        with self.assertRaises(ProbeError):
            control.call_deepseek(control.build_request(view()), "TEST_ONLY_KEY")
        build_opener.return_value.open.assert_called_once()

    def test_default_cli_never_calls_api_even_with_existing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(assistant_step, "ROOT", Path(directory)), \
                    patch.object(assistant_step, "load_engine"), \
                    patch.object(assistant_step, "demo_png", return_value=b"demo"), \
                    patch.object(assistant_step, "observe", return_value=view()), \
                    patch.object(assistant_step, "call_deepseek") as api, \
                    patch.dict("os.environ", {"DEEPSEEK_API_KEY": "TEST_ONLY_KEY"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(assistant_step.main(["--demo"]), 0)
                api.assert_not_called()
            report = json.loads(next(Path(directory).glob("runs/*/report.json")).read_text())
            self.assertEqual(report["api_calls"], 0)
            self.assertEqual(report["actions_performed"], [])


class ConfirmedExecutionTests(unittest.TestCase):
    def run_flow(self, directory, answers, current_hash="old-hash", after_hash="new-hash"):
        observed = view()
        after = copy.deepcopy(observed)
        after["roi_sha256"] = after_hash
        adb = Mock()
        adb.text.side_effect = ["List of devices attached\nUSB123 device transport_id:7\n",
            "mCurrentFocus=Window{123 u0 com.tencent.mm/.plugin.appbrand.ui.AppBrandUI}",
            "mCurrentFocus=Window{123 u0 com.tencent.mm/.plugin.appbrand.ui.AppBrandUI}"]
        adb.select_usb.return_value = {"serial": "USB123", "state": "device", "transport_id": "7"}
        adb.selector.return_value = ("-t", "7")
        image = Mock()
        image.shape = (1400, 900, 3)
        with patch.object(assistant_step, "ROOT", Path(directory)), \
                patch.object(assistant_step, "load_engine"), \
                patch.object(assistant_step, "Adb", return_value=adb), \
                patch.object(assistant_step, "find_adb", return_value=Path("adb.exe")), \
                patch.object(assistant_step, "fresh_capture", return_value=b"test-image"), \
                patch.object(assistant_step, "observe", side_effect=[observed, after]), \
                patch.object(assistant_step, "decode_image", return_value=image), \
                patch.object(assistant_step, "roi_digest", return_value=current_hash), \
                patch.object(assistant_step, "load_api_key", return_value="TEST_ONLY_KEY"), \
                patch.object(assistant_step, "call_deepseek", return_value=(decision(), {})) as api, \
                patch.object(assistant_step.time, "sleep"), \
                patch("builtins.input", side_effect=answers), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = assistant_step.main(["--live", "--roi", "50", "100", "850", "1200", "--allow-api", "--execute"])
        report = json.loads(next(Path(directory).glob("runs/*/report.json")).read_text(encoding="utf-8"))
        return code, adb, api, report

    def test_declining_upload_never_calls_model_or_taps(self):
        with tempfile.TemporaryDirectory() as directory:
            code, adb, api, report = self.run_flow(directory, ["cancel"])
        self.assertEqual(code, 0)
        api.assert_not_called()
        adb.run.assert_not_called()
        self.assertEqual(report["api_calls"], 0)

    def test_declining_tap_never_controls_phone(self):
        with tempfile.TemporaryDirectory() as directory:
            code, adb, api, report = self.run_flow(directory, ["SEND", "cancel"])
        api.assert_called_once()
        adb.run.assert_not_called()
        self.assertEqual(report["status"], "tap_cancelled")

    def test_changed_page_invalidates_previously_confirmed_tap(self):
        with tempfile.TemporaryDirectory() as directory:
            code, adb, api, report = self.run_flow(directory, ["SEND", "TAP t001"], current_hash="changed-page")
        self.assertEqual(code, 2)
        adb.run.assert_not_called()
        self.assertEqual(report["actions_performed"], [])

    def test_one_confirmed_tap_then_stop_without_claiming_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            code, adb, api, report = self.run_flow(directory, ["SEND", "TAP t001"])
        self.assertEqual(code, 0)
        adb.run.assert_called_once_with(*adb.selector.return_value, "shell", "input", "tap", "199", "429")
        self.assertEqual(len(report["actions_performed"]), 1)
        self.assertFalse(report["verification"]["expected_semantic_result_verified"])
        self.assertEqual(report["status"], "tap_sent_change_detected_handoff")

    def test_non_wechat_and_unknown_foreground_stop(self):
        for output in ("mCurrentFocus=null", "mCurrentFocus=Window{123 u0 com.other.app/.Main}"):
            adb = Mock()
            adb.text.return_value = output
            with self.subTest(output=output), self.assertRaises(ProbeError):
                assistant_step.require_wechat(adb, ("-t", "7"))
            adb.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
