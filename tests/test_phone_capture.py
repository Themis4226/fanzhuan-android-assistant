"""Offline failure-path tests; these never contact a phone."""

import argparse
import contextlib
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

import phone_capture as app


def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def png(width=2, height=3, pixels=None):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    if pixels is None:
        pixels = b"\x00" * ((width * 4 + 1) * height)
    return (app.PNG_SIGNATURE + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class ScreenshotTests(unittest.TestCase):
    def test_portrait_and_landscape_dimensions(self):
        for width, height in ((2, 3), (3, 2), (1, 1)):
            with self.subTest(width=width, height=height):
                result = app.validate_png(png(width, height))
                self.assertEqual((result["width"], result["height"]), (width, height))

    def test_adb_error_text_is_not_an_image(self):
        with self.assertRaises(app.ProbeError):
            app.validate_png(b"error: device offline")

    def test_partial_transfer_and_missing_end_are_rejected(self):
        for cut in (1, 8, 12, 25):
            with self.subTest(cut=cut), self.assertRaises(app.ProbeError):
                app.validate_png(png()[:-cut])

    def test_single_byte_corruption_is_rejected(self):
        corrupted = bytearray(png())
        corrupted[40] ^= 1
        with self.assertRaises(app.ProbeError):
            app.validate_png(bytes(corrupted))

    def test_valid_crc_does_not_hide_invalid_pixels(self):
        with self.assertRaises(app.ProbeError):
            app.validate_png(png(pixels=b"\x00"))

    def test_dimension_limit_prevents_large_allocation(self):
        with self.assertRaises(app.ProbeError):
            app.validate_png(png(width=20001, height=1, pixels=b"\x00"))


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.Adb(Path("adb.exe"), 1, Mock())
        self.device = {"serial": "USB123", "state": "device", "transport_id": "7"}

    def test_devices_header_and_wifi_transport_parse(self):
        devices = app.parse_devices(
            "List of devices attached\nUSB123 device model:Phone transport_id:7\n"
            "192.168.1.2:5555 device transport_id:8\nOTHER unauthorized\n")
        self.assertEqual(len(devices), 3)
        self.assertEqual(devices[0]["transport_id"], "7")
        self.assertEqual(devices[2]["state"], "unauthorized")

    def test_usb_selection_is_verified_then_pinned(self):
        self.client.text = Mock(side_effect=["USB123", ""])
        selected = self.client.select_usb([self.device], "USB123")
        self.assertEqual(self.client.text.call_args_list[0].args, ("-d", "get-serialno"))
        self.assertEqual(self.client.text.call_args_list[1].args[:2], ("-t", "7"))
        self.assertEqual(selected, self.device)

    def test_wifi_only_is_rejected(self):
        self.client.text = Mock(side_effect=app.ProbeError("no USB devices"))
        with self.assertRaises(app.ProbeError):
            self.client.select_usb([{"serial": "192.168.1.2:5555", "state": "device"}], None)

    def test_wrong_serial_is_rejected_before_shell_command(self):
        self.client.text = Mock(return_value="USB123")
        with self.assertRaises(app.ProbeError):
            self.client.select_usb([self.device], "ANOTHER")
        self.client.text.assert_called_once_with("-d", "get-serialno")

    def test_unauthorized_and_offline_have_specific_hints(self):
        for state, expected in (("unauthorized", "RSA"), ("offline", "offline")):
            self.client.text = Mock(side_effect=app.ProbeError("unavailable"))
            with self.subTest(state=state), self.assertRaisesRegex(app.ProbeError, expected):
                self.client.select_usb([{"serial": "USB123", "state": state}], None)

    def test_multiple_usb_devices_stop(self):
        self.client.text = Mock(side_effect=app.ProbeError("more than one device"))
        with self.assertRaisesRegex(app.ProbeError, "多台"):
            self.client.select_usb([self.device], None)

    def test_emulator_is_rejected(self):
        self.client.text = Mock(side_effect=["USB123", "1"])
        with self.assertRaisesRegex(app.ProbeError, "模拟器"):
            self.client.select_usb([self.device], None)

    @patch("phone_capture.subprocess.run")
    def test_unplug_during_capture_is_an_error(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"error: device not found")
        with self.assertRaisesRegex(app.ProbeError, "device not found"):
            self.client.run("-t", "7", "exec-out", "screencap", "-p")

    @patch("phone_capture.subprocess.run")
    def test_timeout_stops_without_retry(self, run):
        run.side_effect = subprocess.TimeoutExpired("adb", 1)
        with self.assertRaisesRegex(app.ProbeError, "超时"):
            self.client.run("devices", "-l")
        run.assert_called_once()


class FileSafetyTests(unittest.TestCase):
    def test_explicit_missing_adb_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(app.ProbeError):
                app.find_adb(str(Path(directory) / "absent-adb.exe"))

    def test_report_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "报告.json"
            app.write_json(path, {"status": "first"})
            with self.assertRaises(FileExistsError):
                app.write_json(path, {"status": "second"})
            self.assertIn("first", path.read_text(encoding="utf-8"))

    @patch("phone_capture.Adb")
    def test_failed_capture_never_writes_screen(self, client_class):
        client = client_class.return_value
        client.text.side_effect = ["ADB version", "List of devices attached\nUSB123 device\n", "Phone", "14"]
        client.select_usb.return_value = {"serial": "USB123", "state": "device"}
        client.run.return_value = b"broken screenshot"
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(adb=None, serial=None, timeout=1, command="capture")
            with patch("phone_capture.find_adb", return_value=Path("adb.exe")), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(app.ProbeError):
                    app.execute(args, app.RunLog(Path(directory)), {})
            self.assertFalse((Path(directory) / "screen.png").exists())


if __name__ == "__main__":
    unittest.main()
