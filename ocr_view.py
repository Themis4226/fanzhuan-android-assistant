"""Local OCR observations with original-screen coordinates; no cloud requests."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path

from phone_capture import ProbeError, ROOT, timestamp, validate_png


def check_roi(roi, width: int, height: int) -> tuple[int, int, int, int]:
    bounds = tuple(roi) if roi is not None else (0, 0, width, height)
    if len(bounds) != 4 or any(type(value) is not int for value in bounds):
        raise ProbeError("识别区域必须是四个整数：左 上 右 下。")
    left, top, right, bottom = bounds
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ProbeError("识别区域超出当前屏幕或宽高无效。")
    return bounds


def decode_image(data: bytes):
    import cv2
    import numpy as np

    info = validate_png(data)
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (info["height"], info["width"]):
        raise ProbeError("OpenCV 无法解码截图。")
    return image


def roi_digest(image, roi) -> str:
    left, top, right, bottom = check_roi(roi, image.shape[1], image.shape[0])
    return hashlib.sha256(image[top:bottom, left:right].tobytes()).hexdigest()


def load_engine():
    import rapidocr
    from rapidocr import RapidOCR

    models = Path(rapidocr.__file__).resolve().parent / "models"
    files = {
        "Det.model_path": models / "PP-OCRv6_det_small.onnx",
        "Cls.model_path": models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "Rec.model_path": models / "PP-OCRv6_rec_small.onnx",
    }
    if any(not path.is_file() for path in files.values()):
        raise ProbeError("缺少随 RapidOCR 安装包提供的本地模型，请重装 requirements-ocr.txt；不会自动联网补模型。")
    params = {key: str(path) for key, path in files.items()}
    params.update({"Global.log_level": "error", "Global.text_score": 0.5,
                   "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                   "EngineConfig.onnxruntime.inter_op_num_threads": 1})
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.is_file():
        params["Global.font_path"] = str(font)
    return RapidOCR(params=params)


def normalize_elements(boxes, texts, scores, roi, width: int, height: int) -> list[dict]:
    left, top, right, bottom = check_roi(roi, width, height)
    if not (len(boxes) == len(texts) == len(scores)):
        raise ProbeError("OCR 返回的文本、坐标、置信度数量不一致。")
    elements = []
    for box, text, score in zip(boxes, texts, scores):
        if len(box) != 4 or any(len(point) != 2 for point in box):
            raise ProbeError("OCR 坐标格式无效。")
        coordinates = [float(value) for point in box for value in point]
        score = float(score)
        if not all(math.isfinite(value) for value in coordinates + [score]):
            raise ProbeError("OCR 返回非有限坐标或置信度。")
        if not 0 <= score <= 1:
            raise ProbeError("OCR 置信度超出范围。")
        if not isinstance(text, str) or not text.strip():
            continue
        if (max(point[0] for point in box) <= 0 or min(point[0] for point in box) >= right - left
                or max(point[1] for point in box) <= 0 or min(point[1] for point in box) >= bottom - top):
            raise ProbeError("OCR 文字框位于识别区域之外，已停止。")
        x1 = max(left, min(right - 1, math.floor(min(point[0] for point in box)) + left))
        y1 = max(top, min(bottom - 1, math.floor(min(point[1] for point in box)) + top))
        x2 = max(left + 1, min(right, math.ceil(max(point[0] for point in box)) + left))
        y2 = max(top + 1, min(bottom, math.ceil(max(point[1] for point in box)) + top))
        if x1 >= x2 or y1 >= y2:
            continue
        elements.append({"text": text.strip(), "confidence": round(score, 4),
                         "bounds": [x1, y1, x2, y2],
                         "center": [(x1 + x2 - 1) // 2, (y1 + y2 - 1) // 2]})
    # Stable top-to-bottom order. These are text regions, not confirmed buttons.
    elements.sort(key=lambda item: (item["bounds"][1], item["bounds"][0]))
    for index, element in enumerate(elements, 1):
        element["id"] = f"t{index:03d}"
    return elements


def observe(data: bytes, roi=None, *, engine=None, live: bool = False) -> dict:
    image = decode_image(data)
    height, width = image.shape[:2]
    bounds = check_roi(roi, width, height)
    left, top, right, bottom = bounds
    if engine is None:
        engine = load_engine()
    result = engine(image[top:bottom, left:right])
    if result.txts is None:
        elements = []
    elif result.boxes is None or result.scores is None:
        raise ProbeError("OCR 缺少坐标或置信度，已停止。")
    else:
        elements = normalize_elements(result.boxes, result.txts, result.scores,
                                      bounds, width, height)
    crop_hash = roi_digest(image, bounds)
    identifier = hashlib.sha256((crop_hash + json.dumps(bounds)).encode()).hexdigest()
    return {"schema_version": 1, "observation_id": identifier,
            "observed_at": timestamp(), "live_capture": live,
            "width": width, "height": height, "roi": list(bounds), "roi_sha256": crop_hash,
            "image_sha256": hashlib.sha256(data).hexdigest(), "elements": elements,
            "engine": "RapidOCR", "engine_version": importlib.metadata.version("rapidocr"),
            "note": "OCR text regions; clickability and page meaning require validation"}


def save_observation(directory: Path, view: dict, prefix: str = "observation") -> None:
    from phone_capture import write_json

    write_json(directory / f"{prefix}.json", view)
    with (directory / f"{prefix}.txt").open("x", encoding="utf-8") as stream:
        for item in view["elements"]:
            stream.write(f"{item['id']} | {item['confidence']:.3f} | {item['bounds']} | {item['text']}\n")


def demo_png() -> bytes:
    """Generate a synthetic test screen, not a captured mini-program page."""
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont

    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.is_file():
        raise ProbeError("本地测试页需要 Windows 微软雅黑字体。")
    image = Image.new("RGB", (900, 1400), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 40)
    lines = [(80, 100, "本地 OCR 测试页面"), (80, 240, "请选择 cat 的中文含义"),
             (100, 450, "A. 猫"), (100, 600, "B. 狗"), (100, 750, "C. 鸟"),
             (100, 1000, "下一题")]
    for x, y, text in lines:
        draw.text((x, y), text, fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
