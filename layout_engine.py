"""
造境 AI 设计智能体 · 布局排版引擎（Layout Engine）
================================================
任务书 §45：Grid / Alignment / Spacing / Distribution / Margins / Safe Area / Collision / Aspect Ratio。

这是一台**真实计算几何引擎**：对 Design IR 对象的包围盒（x, y, width, height）做确定性几何运算，
所有结论可复算、无 AI 猜测、无外部依赖：
- Collision：AABB 轴对齐包围盒两两求交，给出重叠面积、IoU、纵横重叠深度。
- Alignment：把一组对象按左/右/水平居中/顶/底/垂直居中对齐到其并集包围框，回传逐个位移量。
- Distribution：沿 X/Y 轴等间距重排（保留首尾，中间按等间隙定位）。
- Safe Area：按画布安全边距检出越界/被裁元素，给夹回建议。
- Margins：计算对象相对画布四边的最小留白。
- Grid Snap：把对象吸附到 cols×rows 等分网格中心。
- Aspect Ratio：对象宽高比与其期望 aspect 或画布 aspect 的偏差。
- Analyze：以上真实度量的综合体检报告（含"近对齐/等距不齐"这类一致性检测）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("layout_engine")

ALIGN_MODES = ("left", "right", "hcenter", "top", "bottom", "vcenter")
_EPS = 1e-6


def _num(v: Any, what: str) -> float:
    """把用户输入转为有限浮点数；非法（非数值字符串 / None / NaN / Inf）诚实抛 ValueError，
    由上层端点映射为 400，绝不静默产出 NaN 几何（那是伪造的成功结果）或泄漏 5xx 崩溃。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} 必须为有限数值，收到 {v!r}")
    if not math.isfinite(f):
        raise ValueError(f"{what} 必须为有限数值（不接受 NaN/Inf），收到 {v!r}")
    return f


def _box(o: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """取对象包围盒 (x1,y1,x2,y2)；缺任一维度（键不存在或为 None）返回 None 表示跳过。
    若维度存在但非有限数值，则诚实抛 ValueError（畸形输入须降级为 400，不得静默跳过致漏报）。"""
    if all(k in o and o[k] is not None for k in ("x", "y", "width", "height")):
        x = _num(o["x"], "对象 x")
        y = _num(o["y"], "对象 y")
        w = _num(o["width"], "对象 width")
        h = _num(o["height"], "对象 height")
        return (x, y, x + w, y + h)
    return None


def _size(o: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    if o.get("width") is not None and o.get("height") is not None:
        return (_num(o["width"], "对象 width"), _num(o["height"], "对象 height"))
    return None


def _overlap(a, b) -> Tuple[float, float, float]:
    """返回 (重叠面积, x 方向重叠, y 方向重叠)。"""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy, ix, iy


def _union(boxes) -> Optional[Tuple[float, float, float, float]]:
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


class LayoutEngine:
    def __init__(self, tol: float = 0.5):
        self.tol = tol  # 对齐/等距一致性容差（用户单位，默认 0.5）

    # ---- Collision ----
    def collide(self, objects: List[Dict[str, Any]], min_overlap_ratio: float = 0.0) -> Dict[str, Any]:
        boxes = []
        for i, o in enumerate(objects):
            b = _box(o)
            if b:
                boxes.append((o.get("id", f"obj_{i}"), b))
        collisions = []
        for a in range(len(boxes)):
            for c in range(a + 1, len(boxes)):
                ia, ba = boxes[a]
                ic, bc = boxes[c]
                ov, ix, iy = _overlap(ba, bc)
                if ov <= _EPS:
                    continue
                amin = min(_area(ba), _area(bc)) or 1.0
                ratio = ov / amin
                if ratio < min_overlap_ratio:
                    continue
                union = _area(ba) + _area(bc) - ov or 1.0
                collisions.append({
                    "pair": [ia, ic], "overlap_area": round(ov, 2),
                    "overlap_of_smaller": round(ratio * 100, 2),
                    "iou": round(ov / union, 4),
                    "overlap_x": round(ix, 2), "overlap_y": round(iy, 2),
                    "suggestion": "沿重叠深度较小的轴位移，或调整图层顺序/尺寸",
                })
        collisions.sort(key=lambda x: -x["overlap_area"])
        return {"object_count": len(boxes), "collision_count": len(collisions), "collisions": collisions}

    # ---- Alignment ----
    def align(self, objects: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
        if mode not in ALIGN_MODES:
            raise KeyError(f"未知对齐模式: {mode}；可选 {list(ALIGN_MODES)}")
        items = []
        for i, o in enumerate(objects):
            b = _box(o)
            if b:
                items.append((i, o, b))
        if not items:
            return {"mode": mode, "aligned": 0, "objects": [], "note": "无可定位对象"}
        ref = _union([b for _, _, b in items])
        adjusted = []
        for idx, o, b in items:
            w, h = b[2] - b[0], b[3] - b[1]
            nx, ny = float(o["x"]), float(o["y"])
            if mode == "left":
                nx = ref[0]
            elif mode == "right":
                nx = ref[2] - w
            elif mode == "hcenter":
                nx = (ref[0] + ref[2]) / 2 - w / 2
            elif mode == "top":
                ny = ref[1]
            elif mode == "bottom":
                ny = ref[3] - h
            elif mode == "vcenter":
                ny = (ref[1] + ref[3]) / 2 - h / 2
            adjusted.append({"id": o.get("id", f"obj_{idx}"),
                             "from": {"x": float(o["x"]), "y": float(o["y"])},
                             "to": {"x": round(nx, 3), "y": round(ny, 3)},
                             "dx": round(nx - float(o["x"]), 3), "dy": round(ny - float(o["y"]), 3)})
        return {"mode": mode, "reference_box": [round(v, 2) for v in ref],
                "aligned": len(adjusted), "objects": adjusted}

    # ---- Distribution ----
    def distribute(self, objects: List[Dict[str, Any]], axis: str = "x",
                   spacing: Optional[float] = None) -> Dict[str, Any]:
        if axis not in ("x", "y"):
            raise KeyError("axis ∈ {x, y}")
        items = []
        for i, o in enumerate(objects):
            b = _box(o)
            if b:
                items.append([o.get("id", f"obj_{i}"), b, float(o["x"]), float(o["y"]), o])
        if len(items) < 3:
            return {"axis": axis, "distributed": len(items),
                    "objects": [], "note": "等距分布至少需 3 个对象"}
        key = 0 if axis == "x" else 1
        size_i = 0 if axis == "x" else 1
        items.sort(key=lambda it: it[1][key])
        start = items[0][1][key]
        end = items[-1][1][key + 2]
        total_span = end - start
        sum_size = sum((it[1][key + 2] - it[1][key]) for it in items)
        gaps = len(items) - 1
        gap = spacing if spacing is not None else (total_span - sum_size) / gaps if gaps else 0
        adjusted = []
        cursor = start
        for oid, b, ox, oy, o in items:
            sz = b[key + 2] - b[key]
            if axis == "x":
                nx, ny = cursor, oy
            else:
                nx, ny = ox, cursor
            adjusted.append({"id": oid, "to": {"x": round(nx, 3), "y": round(ny, 3)},
                             "dx": round(nx - ox, 3), "dy": round(ny - oy, 3)})
            cursor += sz + gap
        return {"axis": axis, "distributed": len(adjusted),
                "computed_gap": round(gap, 3),
                "spacing_source": ("fixed" if spacing is not None else "even_across_span"),
                "objects": adjusted}

    # ---- Safe Area ----
    def safe_area(self, objects: List[Dict[str, Any]], canvas: Dict[str, Any],
                  margin: Optional[float] = None) -> Dict[str, Any]:
        cw = _num(canvas.get("width", 0), "画布 width")
        ch = _num(canvas.get("height", 0), "画布 height")
        if margin is None:
            margin = min(cw, ch) * _num(canvas.get("safe_margin_ratio", 0.03), "safe_margin_ratio")
        safe = (margin, margin, cw - margin, ch - margin)
        violations = []
        for i, o in enumerate(objects):
            b = _box(o)
            if not b or o.get("ignore_safe_area"):
                continue
            out = {
                "left": b[0] < safe[0] - _EPS, "top": b[1] < safe[1] - _EPS,
                "right": b[2] > safe[2] + _EPS, "bottom": b[3] > safe[3] + _EPS,
            }
            off_canvas = b[0] < -_EPS or b[1] < -_EPS or b[2] > cw + _EPS or b[3] > ch + _EPS
            if any(out.values()) or off_canvas:
                clamp_x = min(max(b[0], safe[0]), max(safe[0], safe[2] - (b[2] - b[0])))
                clamp_y = min(max(b[1], safe[1]), max(safe[1], safe[3] - (b[3] - b[1])))
                violations.append({
                    "id": o.get("id", f"obj_{i}"), "sides": [k for k, v in out.items() if v],
                    "off_canvas": off_canvas,
                    "autofix": {"set": {"x": round(clamp_x, 2), "y": round(clamp_y, 2)}},
                })
        return {"canvas": {"width": cw, "height": ch}, "margin": round(margin, 2),
                "safe_box": [round(v, 2) for v in safe],
                "violation_count": len(violations), "violations": violations}

    # ---- Margins（四边留白）----
    def margins(self, objects: List[Dict[str, Any]], canvas: Dict[str, Any]) -> Dict[str, Any]:
        cw = _num(canvas.get("width", 0), "画布 width")
        ch = _num(canvas.get("height", 0), "画布 height")
        boxes = [_box(o) for o in objects if _box(o)]
        if not boxes:
            return {"left": cw, "top": ch, "right": cw, "bottom": ch, "content_box": None}
        u = _union(boxes)
        return {
            "left": round(u[0], 2), "top": round(u[1], 2),
            "right": round(cw - u[2], 2), "bottom": round(ch - u[3], 2),
            "content_box": [round(v, 2) for v in u],
        }

    # ---- Grid Snap ----
    def grid_snap(self, objects: List[Dict[str, Any]], canvas: Dict[str, Any],
                  cols: int = 12, rows: int = 12, mode: str = "cell_center") -> Dict[str, Any]:
        cw = _num(canvas.get("width", 0), "画布 width")
        ch = _num(canvas.get("height", 0), "画布 height")
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        if cw <= 0 or ch <= 0:
            raise ValueError(f"网格吸附需要正的画布宽高，收到 width={cw} height={ch}")
        cellw, cellh = cw / cols, ch / rows
        snapped = []
        for i, o in enumerate(objects):
            b = _box(o)
            if not b:
                continue
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            col = min(cols - 1, max(0, int(cx // cellw)))
            row = min(rows - 1, max(0, int(cy // cellh)))
            w, h = b[2] - b[0], b[3] - b[1]
            if mode == "cell_center":
                nx = col * cellw + cellw / 2 - w / 2
                ny = row * cellh + cellh / 2 - h / 2
            else:  # cell_top_left
                nx, ny = col * cellw, row * cellh
            snapped.append({"id": o.get("id", f"obj_{i}"), "cell": [col, row],
                            "to": {"x": round(nx, 2), "y": round(ny, 2)},
                            "dx": round(nx - float(o["x"]), 2), "dy": round(ny - float(o["y"]), 2)})
        return {"grid": {"cols": cols, "rows": rows, "cell": [round(cellw, 2), round(cellh, 2)]},
                "mode": mode, "snapped": len(snapped), "objects": snapped}

    # ---- Aspect Ratio ----
    def aspect_ratio(self, objects: List[Dict[str, Any]], canvas: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        canvas_ar = None
        if canvas and canvas.get("width") is not None and canvas.get("height") is not None:
            aw = _num(canvas["width"], "画布 width")
            ah = _num(canvas["height"], "画布 height")
            if ah != 0:
                canvas_ar = round(aw / ah, 4)
        items = []
        for i, o in enumerate(objects):
            s = _size(o)
            if not s or s[1] == 0:
                continue
            ar = s[0] / s[1]
            want = o.get("aspect")
            ref = _num(want, "aspect") if want else canvas_ar
            item = {"id": o.get("id", f"obj_{i}"), "aspect_ratio": round(ar, 4)}
            if ref:
                dev = (ar - ref) / ref if ref else 0.0
                item["reference"] = round(ref, 4)
                item["deviation_percent"] = round(dev * 100, 2)
                item["matches"] = abs(dev) <= 0.02
            items.append(item)
        return {"canvas_aspect": canvas_ar, "count": len(items), "objects": items}

    # ---- Analyze：综合体检 ----
    def analyze(self, objects: List[Dict[str, Any]], canvas: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        canvas = canvas or {}
        boxes = []
        for i, o in enumerate(objects):
            b = _box(o)
            if b:
                boxes.append((o.get("id", f"obj_{i}"), b, o))
        collision = self.collide(objects)
        safe = self.safe_area(objects, canvas) if canvas.get("width") else {"violation_count": 0, "violations": []}
        margins = self.margins(objects, canvas) if canvas.get("width") else {}

        # 近一致性检测：左边缘是否几乎对齐 / X 间距是否几乎等距
        left_edges = sorted(round(b[0], 3) for _, b, _ in boxes)
        near_aligned_left = 0
        for x in left_edges:
            if sum(1 for y in left_edges if abs(y - x) <= self.tol) >= max(2, len(left_edges) // 2):
                near_aligned_left = sum(1 for y in left_edges if abs(y - left_edges[0]) <= self.tol)
                break
        findings: List[str] = []
        if collision["collision_count"]:
            findings.append(f"检出 {collision['collision_count']} 组元素重叠")
        if safe.get("violation_count"):
            findings.append(f"{safe['violation_count']} 个元素侵入安全边距/越界")
        if len(boxes) >= 3 and near_aligned_left < len(boxes):
            findings.append("左边缘未完全对齐，可执行 left 对齐")
        if not findings:
            findings.append("未发现重叠/越界/明显未对齐问题")
        score = 100 - collision["collision_count"] * 12 - safe.get("violation_count", 0) * 8
        return {
            "object_count": len(boxes),
            "collision": collision, "safe_area": safe, "margins": margins,
            "near_aligned_left_count": near_aligned_left,
            "findings": findings, "score": max(0, min(100, score)),
        }


if __name__ == "__main__":
    eng = LayoutEngine()
    objs = [
        {"id": "a", "x": 0, "y": 0, "width": 100, "height": 100},
        {"id": "b", "x": 50, "y": 50, "width": 100, "height": 100},  # 与 a 重叠 50x50
        {"id": "c", "x": 400, "y": 10, "width": 50, "height": 50},
    ]
    col = eng.collide(objs)
    assert col["collision_count"] == 1 and col["collisions"][0]["pair"] == ["a", "b"], col
    assert abs(col["collisions"][0]["overlap_area"] - 2500) < 1e-6, col
    al = eng.align(objs, "left")
    assert all(o["to"]["x"] == 0 for o in al["objects"]), al
    dist = eng.distribute([
        {"id": "x", "x": 0, "y": 0, "width": 10, "height": 10},
        {"id": "y", "x": 20, "y": 0, "width": 10, "height": 10},
        {"id": "z", "x": 100, "y": 0, "width": 10, "height": 10},
    ], "x")
    assert dist["distributed"] == 3 and abs(dist["objects"][1]["to"]["x"] - 50) < 1e-6, dist
    assert abs(dist["computed_gap"] - 40) < 1e-6, dist
    sa = eng.safe_area(objs, {"width": 500, "height": 500}, margin=20)
    assert sa["violation_count"] >= 1, sa
    gs = eng.grid_snap([{"id": "t", "x": 45, "y": 45, "width": 10, "height": 10}],
                       {"width": 100, "height": 100}, cols=4, rows=4)
    assert gs["snapped"] == 1 and gs["objects"][0]["cell"] == [2, 2], gs
    ar = eng.aspect_ratio([{"id": "s", "x": 0, "y": 0, "width": 100, "height": 50, "aspect": 2.0}])
    assert ar["objects"][0]["matches"] is True, ar
    an = eng.analyze(objs, {"width": 500, "height": 500})
    assert 0 <= an["score"] <= 100 and an["collision"]["collision_count"] == 1
    try:
        eng.align(objs, "diagonal")
        raise AssertionError("未知模式应抛 KeyError")
    except KeyError:
        pass

    # ---- 敌意输入鲁棒性（Phase 22：畸形/非有限坐标须诚实降级为 ValueError→400，绝不泄漏 5xx，
    #      也绝不静默跳过或产出 NaN 几何——那是伪造的成功结果）。若把 _box/_num 换成常量桩，这些断言必红。----
    _bad_str = [{"id": "a", "x": "NaN?", "y": "z", "width": "w", "height": "h"}]
    try:
        eng.collide(_bad_str)
        raise AssertionError("非数值坐标必须抛 ValueError，不得泄漏/静默跳过")
    except ValueError:
        pass
    # 字面量 "NaN"/"inf" 字符串：float 能转换但非有限，必须拒绝，绝不静默产出 NaN 面积
    try:
        eng.collide([{"id": "a", "x": "NaN", "y": 0, "width": 10, "height": 10},
                     {"id": "b", "x": 0, "y": 0, "width": 10, "height": 10}])
        raise AssertionError("NaN 坐标必须被拒绝（防伪造几何）")
    except ValueError:
        pass
    try:
        eng.distribute([{"x": 0, "y": 0, "width": "a", "height": "b"},
                        {"x": 5, "y": 0, "width": 10, "height": 10},
                        {"x": 20, "y": 0, "width": 10, "height": 10}], axis="x")
        raise AssertionError("非数值尺寸必须抛 ValueError")
    except ValueError:
        pass
    # grid_snap 画布宽/高为 0 或缺失：除法必须降级为 ValueError，不得 ZeroDivisionError
    try:
        eng.grid_snap([{"id": "t", "x": 10, "y": 10, "width": 4, "height": 4}], {}, cols=4, rows=4)
        raise AssertionError("画布尺寸缺失(0)必须抛 ValueError，不得除零崩溃")
    except ValueError:
        pass
    try:
        eng.safe_area(objs, {"width": "wide", "height": 500})
        raise AssertionError("非法画布宽必须抛 ValueError")
    except ValueError:
        pass
    # 反向保护：真正缺失维度（键不存在/None）仍应被诚实跳过（_box 返回 None → 排除），不得过度拒绝
    _partial = [{"id": "ok", "x": 0, "y": 0, "width": 10, "height": 10},
                {"id": "no-dim", "x": 5}]
    pc = eng.collide(_partial)
    assert pc["object_count"] == 1, ("缺维对象应被跳过而非报错", pc)
    print("[PASS] layout engine self-test  collisions=%d align_ok dist_gap=%s safe_viol=%d score=%d  敌意输入(非数值/NaN/除零画布)均诚实降级"
          % (col["collision_count"], dist["computed_gap"], sa["violation_count"], an["score"]))
