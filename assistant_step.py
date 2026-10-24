"""One OCR -> model suggestion -> confirmed tap -> verification step. No task loop."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import re
import sys
import time

from phone_capture import Adb, ProbeError, ROOT, RunLog, find_adb, parse_devices, timestamp, write_json
from ocr_view import decode_image, demo_png, load_engine, observe, roi_digest, save_observation
from model_control import (ENDPOINT, MAX_OUTPUT_TOKENS, MODEL, build_request, call_deepseek,
                           load_api_key, validate_decision)


def fresh_capture(adb, selector) -> bytes:
    raw = adb.run(*selector, "exec-out", "screencap", "-p")
    decode_image(raw)
    return raw


def require_wechat(adb, selector) -> None:
    windows = adb.text(*selector, "shell", "dumpsys", "window")
    focused = re.search(r"mCurrentFocus=Window\{[^\n}]*\s([A-Za-z0-9_.]+)/", windows)
    if not focused or focused.group(1) != "com.tencent.mm":
        raise ProbeError("当前前台无法确认为微信，已停止，不发送点击。")


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description="本地 OCR 视图与 DeepSeek 单步操作；默认不调用 API、不点击")
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="使用本地合成练习页测试 OCR")
    source.add_argument("--image", type=Path, help="本地 PNG，只做识别/建议，不能操作手机")
    source.add_argument("--live", action="store_true", help="读取 USB 手机当前页面")
    cli.add_argument("--roi", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                     help="只识别屏幕指定矩形区域；发送真机文字给 API 时必须指定")
    cli.add_argument("--keep-image", action="store_true", help="额外保存本次画面；默认只保留 OCR/日志")
    cli.add_argument("--allow-api", action="store_true", help="允许在本机确认发送后调用一次付费 API")
    cli.add_argument("--execute", action="store_true", help="允许人工确认后执行一次模型建议的点击")
    cli.add_argument("--adb")
    cli.add_argument("--serial")
    args = cli.parse_args(argv)
    if args.execute and not (args.live and args.allow_api):
        cli.error("--execute 需要同时指定 --live 和 --allow-api")
    if args.allow_api and args.live and args.roi is None:
        cli.error("发送真机 OCR 前必须用 --roi 限定题目和选项范围，排除无关信息")
    directory = ROOT / "runs" / (dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_ocr")
    directory.mkdir(parents=True, exist_ok=False)
    log = RunLog(directory)
    report = {"started_at": timestamp(), "status": "started", "api_calls": 0,
              "actions_performed": [], "image_saved": False, "model": MODEL}
    exit_code = 0
    try:
        engine = load_engine()
        adb = selector = None
        if args.live:
            adb = Adb(find_adb(args.adb), 20, log)
            devices = parse_devices(adb.text("devices", "-l"))
            device = adb.select_usb(devices, args.serial)
            selector = adb.selector(device)
            report["device"] = device
            if args.execute:
                require_wechat(adb, selector)
            data = fresh_capture(adb, selector)
        elif args.image:
            data = args.image.resolve().read_bytes()
        else:
            data = demo_png()
        if args.keep_image:
            (directory / "screen.png").write_bytes(data)
            report["image_saved"] = True
        view = observe(data, args.roi, engine=engine, live=args.live)
        save_observation(directory, view)
        report["observation_id"] = view["observation_id"]
        report["text_regions"] = len(view["elements"])
        log.event("ocr_complete", count=len(view["elements"]), image_saved=report["image_saved"])
        print(f"OCR 完成：{len(view['elements'])} 个文字区域；文字与位置见 {directory / 'observation.txt'}")
        body = build_request(view)
        write_json(directory / "request.preview.json", body)
        if not args.allow_api:
            report["status"] = "ocr_ready_api_disabled"
            print("本次未调用模型 API、未点击。request.preview.json 是本地请求预览。")
            return 0
        key = load_api_key()
        print(f"将把识别区域的文字、位置及置信度发送到 {ENDPOINT}；不发送图片或密钥到日志。")
        print(f"这是付费调用：最多一次、不重试，输出上限 {MAX_OUTPUT_TOKENS} tokens。")
        print(f"请先本地检查 {directory / 'request.preview.json'}，确认不含无关个人信息。")
        if input("确认发送请输入 SEND，其余输入取消：").strip() != "SEND":
            report["status"] = "api_cancelled"
            return 0
        report["api_calls"] = 1  # An attempted request may be billed even if it times out.
        decision, usage = call_deepseek(body, key)
        write_json(directory / "usage.json", usage)
        write_json(directory / "decision.json", decision)
        proposal = validate_decision(view, decision)
        write_json(directory / "proposal.json", proposal)
        print(f"模型建议：{decision['action']}；理由：{decision['reason']}")
        report["status"] = "suggestion_ready_no_action"
        if not args.execute or proposal["action"] != "tap":
            return 0
        target_id = proposal["target_id"]
        print(f"拟点击 {target_id}：{proposal['text']}，位置 ({proposal['x']}, {proposal['y']})")
        if input(f"确认此次答题/操作请输入 TAP {target_id}，其余输入取消：").strip() != f"TAP {target_id}":
            report["status"] = "tap_cancelled"
            return 0
        require_wechat(adb, selector)
        current = decode_image(fresh_capture(adb, selector))
        if (current.shape[:2] != (view["height"], view["width"])
                or roi_digest(current, view["roi"]) != view["roi_sha256"]):
            raise ProbeError("等待期间题目区域或屏幕方向发生变化，旧点击建议已作废；未发送点击。")
        adb.run(*selector, "shell", "input", "tap", str(proposal["x"]), str(proposal["y"]))
        report["actions_performed"].append({"action": "tap", "target_id": target_id,
            "text": proposal["text"], "x": proposal["x"], "y": proposal["y"],
            "confirmed_by_user": True, "at": timestamp()})
        log.event("confirmed_tap", target_id=target_id, x=proposal["x"], y=proposal["y"])
        time.sleep(0.8)
        after_data = fresh_capture(adb, selector)
        after_image = decode_image(after_data)
        if after_image.shape[:2] != (view["height"], view["width"]):
            report["status"] = "tap_sent_orientation_changed_handoff"
            print("点击后屏幕方向或尺寸变化，已停止，请人工检查。")
            return 0
        after = observe(after_data, view["roi"], engine=engine, live=True)
        save_observation(directory, after, "after")
        changed = after["roi_sha256"] != view["roi_sha256"]
        report["verification"] = {"roi_changed": changed,
            "text_changed": [v["text"] for v in view["elements"]] != [v["text"] for v in after["elements"]],
            "expected_semantic_result_verified": False}
        report["status"] = "tap_sent_change_detected_handoff" if changed else "tap_sent_no_change_handoff"
        if args.keep_image:
            (directory / "after.png").write_bytes(after_data)
        print("已发送一次点击并重新识别页面，流程停止。页面变化不能证明答题正确或任务完成，请人工核对。")
        return 0
    except (ProbeError, OSError, ImportError, ValueError, RuntimeError) as exc:
        report.update(status="failed", error=str(exc))
        log.event("failure", reason=str(exc))
        print(f"已停止：{exc}", file=sys.stderr)
        exit_code = 2
    except (KeyboardInterrupt, EOFError):
        report["status"] = "cancelled"
        exit_code = 130
    finally:
        report["finished_at"] = timestamp()
        write_json(directory / "report.json", report)
        print(f"本地报告：{directory / 'report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
