"""USB-only Android screenshot prototype. Python standard library only."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parent
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ProbeError(RuntimeError):
    """A recoverable connection or screenshot problem."""


def timestamp() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class RunLog:
    def __init__(self, directory: Path):
        self.directory = directory

    def event(self, name: str, **fields: object) -> None:
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": timestamp(), "event": name, **fields},
                                    ensure_ascii=False) + "\n")


def find_adb(explicit: str | None) -> Path:
    # An explicit but invalid path must fail instead of silently choosing another tool.
    configured = explicit or os.environ.get("ADB_PATH")
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise ProbeError(f"ADB 路径不存在：{path}。请检查 --adb 或 ADB_PATH。")
    local = ROOT / "tools" / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
    if local.is_file():
        return local
    for bundled in sorted((ROOT / "tools").glob("scrcpy*/adb.exe")):
        if bundled.is_file():
            return bundled
    located = shutil.which("adb")
    if located:
        return Path(located).resolve()
    raise ProbeError("未找到 ADB。请按 README 下载官方 Platform-Tools，或指定 --adb。")


def parse_devices(output: str) -> list[dict[str, str]]:
    devices = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or line.startswith(("List of devices", "*")):
            continue
        item = {"serial": fields[0], "state": fields[1]}
        item.update(field.split(":", 1) for field in fields[2:] if ":" in field)
        devices.append(item)
    return devices


def connection_hint(devices: list[dict[str, str]]) -> str:
    if any(item["state"] == "unauthorized" for item in devices):
        return "手机尚未授权：解锁手机，允许 USB 调试的 RSA 授权，再重试。"
    if any(item["state"] == "offline" for item in devices):
        return "ADB 设备处于 offline：重新插拔数据线、检查 USB 调试后重试。"
    return ("未找到唯一的已授权 USB 真机：请使用数据线连接，开启 USB 调试并授权。"
            "若有多台 USB 设备，请暂时只保留目标手机；无线设备不能用于本次验证。")


class Adb:
    def __init__(self, executable: Path, timeout: float, log: RunLog):
        self.executable, self.timeout, self.log = executable, timeout, log

    def run(self, *args: str) -> bytes:
        started = time.perf_counter()
        try:
            result = subprocess.run(
                [str(self.executable), *args], capture_output=True, timeout=self.timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            self.log.event("adb_timeout", args=list(args), timeout_seconds=self.timeout)
            raise ProbeError(f"ADB 操作超时（{self.timeout:g} 秒），已停止；检查 USB 连接和手机状态。") from exc
        except OSError as exc:
            raise ProbeError(f"无法启动 ADB：{exc}") from exc
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        self.log.event("adb_command", args=list(args), returncode=result.returncode,
                       elapsed_ms=elapsed, stdout_bytes=len(result.stdout))
        if result.returncode:
            detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise ProbeError(f"ADB 失败：{detail}")
        return result.stdout

    def text(self, *args: str) -> str:
        return self.run(*args).decode("utf-8", errors="replace").strip()

    def select_usb(self, devices: list[dict[str, str]], requested: str | None) -> dict[str, str]:
        # On Windows get-devpath may say 'unknown', and devices -l may omit usb:.
        # -d itself selects a single USB transport and excludes TCP/IP devices.
        try:
            serial = self.text("-d", "get-serialno")
        except ProbeError as exc:
            raise ProbeError(f"{connection_hint(devices)}\n{exc}") from exc
        if requested and serial != requested:
            raise ProbeError("--serial 指定的设备与当前唯一 USB 设备不符，已停止。")
        matches = [d for d in devices if d["serial"] == serial and d["state"] == "device"]
        if len(matches) != 1 or serial.startswith("emulator-") or ":" in serial:
            raise ProbeError(connection_hint(devices))
        device = matches[0]
        selector = self.selector(device)
        if self.text(*selector, "shell", "getprop", "ro.kernel.qemu") == "1":
            raise ProbeError("检测到模拟器，本原型只接受 USB 真机。")
        return device

    @staticmethod
    def selector(device: dict[str, str]) -> tuple[str, str]:
        # Pin the verified transport to avoid silently selecting a different phone.
        if device.get("transport_id"):
            return "-t", device["transport_id"]
        return "-s", device["serial"]


def validate_png(data: bytes) -> dict[str, int]:
    """Validate chunks, CRCs, dimensions and non-interlaced pixel stream locally."""
    if not data.startswith(PNG_SIGNATURE):
        raise ProbeError("截图不是 PNG；可能是 ADB 返回错误文本，未保存为截图。")
    position, header, compressed, ended = 8, None, bytearray(), False
    while position < len(data):
        if position + 12 > len(data):
            raise ProbeError("PNG 截图被截断。")
        size = struct.unpack_from(">I", data, position)[0]
        end = position + 12 + size
        if end > len(data):
            raise ProbeError("PNG 数据块不完整。")
        kind = data[position + 4:position + 8]
        payload = data[position + 8:end - 4]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ProbeError("PNG CRC 校验失败；请检查连接后重新截图。")
        if position == 8 and kind != b"IHDR":
            raise ProbeError("PNG 首块不是 IHDR。")
        if kind == b"IHDR":
            if header is not None or size != 13:
                raise ProbeError("PNG IHDR 无效。")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            if size or end != len(data):
                raise ProbeError("PNG 结束块无效。")
            ended = True
            break
        position = end
    if not ended or header is None or not compressed:
        raise ProbeError("PNG 缺少必要的数据块。")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if (not channels or depth not in (8, 16) or compression or filtering or interlace
            or not (0 < width <= 20000 and 0 < height <= 20000)
            or width * height > 32000000):
        raise ProbeError("PNG 尺寸或像素格式不在原型支持范围（8/16 位非交错灰度/RGB/RGBA）。")
    stride = (width * channels * depth + 7) // 8 + 1
    expected = stride * height
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise ProbeError("PNG 像素数据解压失败。") from exc
    if len(pixels) != expected or not decoder.eof or decoder.unused_data:
        raise ProbeError("PNG 像素流长度或结束标记无效。")
    if any(pixels[row * stride] > 4 for row in range(height)):
        raise ProbeError("PNG 扫描行过滤器无效。")
    return {"width": width, "height": height, "bit_depth": depth, "color_type": color}


def environment() -> dict:
    scrcpy = shutil.which("scrcpy")
    for local in sorted((ROOT / "tools").glob("scrcpy*/scrcpy.exe")):
        scrcpy = str(local)
        break
    result = {
        "python": sys.version.split()[0], "python_executable": sys.executable,
        "python_bits": struct.calcsize("P") * 8,
        "scrcpy_path": scrcpy,
        "optional_modules": {name: importlib.util.find_spec(name) is not None for name in
                             ("cv2", "rapidocr", "rapidocr_onnxruntime", "paddleocr")},
        "scope": "USB connection and screenshot only; no input, OCR or AI calls",
    }
    if scrcpy:
        try:
            proc = subprocess.run([scrcpy, "--version"], capture_output=True, timeout=10,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            result["scrcpy_version"] = (proc.stdout + proc.stderr).decode("utf-8", errors="replace").strip()
            result["scrcpy_version_check_ok"] = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["scrcpy_version_check_ok"] = False
            result["scrcpy_error"] = str(exc)
    return result


def execute(args: argparse.Namespace, log: RunLog, report: dict) -> Path | None:
    report["environment"] = environment()
    adb = Adb(find_adb(args.adb), args.timeout, log)
    report["adb_path"] = str(adb.executable)
    report["adb_version"] = adb.text("version")
    devices = parse_devices(adb.text("devices", "-l"))
    report["devices"] = devices
    device = adb.select_usb(devices, args.serial)
    selector = adb.selector(device)
    report["device"] = {**device, "connection": "usb", "selection": "adb -d get-serialno"}
    report["device"]["model"] = adb.text(*selector, "shell", "getprop", "ro.product.model")
    report["device"]["android_version"] = adb.text(*selector, "shell", "getprop", "ro.build.version.release")
    report["usb_ready"] = True
    print(f"USB 真机已连接：{report['device']['model']} / Android {report['device']['android_version']}")
    if not report["environment"]["scrcpy_path"]:
        print("提示：未在 PATH/项目 tools 中发现 scrcpy；不影响当前截图，请见 README 安装步骤。")
    if args.command == "doctor":
        report["status"] = "usb_ready_screenshot_not_tested"
        return None
    started = time.perf_counter()
    # Keep raw bytes throughout. PowerShell text redirection can corrupt PNG data.
    data = adb.run(*selector, "exec-out", "screencap", "-p")
    image_info = validate_png(data)
    path = log.directory / "screen.png"
    with path.open("xb") as stream:
        stream.write(data)
    report["screenshot"] = {
        **image_info, "path": str(path), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "validation": "PNG chunks + CRC + decoded scanline length; content requires local viewing",
    }
    report["status"] = "screenshot_validated"
    log.event("screenshot_saved", **report["screenshot"])
    print(f"截图已保存并校验：{path}\n尺寸：{image_info['width']} × {image_info['height']}")
    return path


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="安卓 USB 连接诊断与截图（无点击、无联网 API）")
    cli.add_argument("--adb", help="adb.exe 的完整路径；优先于 ADB_PATH 和项目内工具")
    cli.add_argument("--serial", help="核对目标 USB 序列号（仍要求只连接一台 USB 设备）")
    cli.add_argument("--timeout", type=float, default=20, help="单条 ADB 命令超时秒数，默认 20")
    cli.add_argument("--output-dir", type=Path, default=ROOT / "runs", help="本地报告根目录")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查环境和 USB，不截图")
    capture = sub.add_parser("capture", help="获取并校验一次当前屏幕截图")
    capture.add_argument("--open", action="store_true", help="使用 Windows 默认图片查看器打开截图")
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if not 0 < args.timeout <= 300:
        cli.error("--timeout 必须大于 0 且不超过 300 秒")
    run_dir = args.output_dir.expanduser().resolve() / dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"无法创建报告目录：{exc}", file=sys.stderr)
        return 2
    log = RunLog(run_dir)
    report = {"started_at": timestamp(), "command": args.command, "usb_ready": False,
              "status": "started", "actions_performed": [], "run_directory": str(run_dir)}
    log.event("start", command=args.command)
    exit_code = 0
    try:
        image_path = execute(args, log, report)
        if getattr(args, "open", False) and image_path:
            try:
                if os.name != "nt":
                    raise OSError("--open 当前仅支持 Windows；请手动打开 PNG")
                os.startfile(str(image_path))
                report["viewer"] = "open_requested; visual confirmation pending"
            except OSError as exc:
                report["viewer"] = f"open_failed: {exc}"
                print(f"截图成功，自动打开失败：{exc}；可手动打开以上路径。")
    except (ProbeError, OSError) as exc:
        report.update(status="failed", error=str(exc))
        log.event("failure", reason=str(exc))
        print(f"已停止：{exc}", file=sys.stderr)
        exit_code = 2
    except KeyboardInterrupt:
        report.update(status="cancelled", error="用户取消")
        exit_code = 130
    finally:
        report["finished_at"] = timestamp()
        write_json(run_dir / "report.json", report)
        print(f"本地报告：{run_dir / 'report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
