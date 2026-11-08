"""panel_recognition_engine.py — 第 25 引擎：栅格刀版图 → 面板/线型/拓扑的真实几何反解。

能力真值表 Panel Recognition 的 raster 半边自 Phase 47 起为**真实实现**：
输入一张位图刀版（轴对齐矩形面板族），输出可逐项复算的面板矩形、cut/fold 线型与
邻接拓扑。全程**不使用任何 CV/ML 模型**——方法为三段确定性几何：

1. Otsu 直方图类间方差最大化自算二值化阈值（阈值随响应回显，不写死）；
2. 轴对齐线抽取：按像素的水平/垂直游程长度与垂直/水平游程厚度双门限分离"线"与
   "块状墨迹"，再把共线短段按间隙合并为段，用**实测沿线覆盖率**判型
   （coverage≥solid 阈值→实线=cut，出现周期性缺口→虚线=fold）；
3. 线掩码取反做游程并查集连通域：**屏障 = 线像素 ∪ 合并段跨度**（虚线的 dash 缺口按
   几何支撑补齐，否则虚线在拓扑上不封闭、面板会连成一片；线型判定仍用真实墨覆盖率），
   触边者判环境背景并排除，其余封闭域即面板候选；
   逐面板输出 bbox/面积/bbox 填充率，面板间共边求交得邻接、度序列与折叠连通分量，
   外轮廓长由占用格游程数精确算得（2×行游程 + 2×列游程）。

§2 红线口径：
- 邻边线型与面板角色一律标 line_basis/role_basis="geometric-heuristic"，只声称几何
  事实，绝不谎称"识别出正面/背面"等语义角色；
- 无墨/无线/无封闭域时如实返回 no_panels_detected 与空列表，min_area 以下入 rejected
  并给中文原因，绝不臆造型；
- 能力边界显式写入 caveats 而非沉默：本方法覆盖**轴对齐矩形刀版**（直插盒、
  自锁底展开的主要形态），斜角、曲线、非直线轮廓不在能力内；
- 结果确定性：同一输入两次调用逐字段全等（无随机、无时序依赖）。
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    PIL_AVAILABLE = False

ENGINE_NAME = "panel_recognition_engine"
METHOD = ("otsu-binarize + axis-aligned-run-line-extraction + collinear-gap-merge "
          "+ dashed-support-barrier + run-based-unionfind-components")
LINE_BASIS = "geometric-heuristic"
CAVEATS = [
    "覆盖轴对齐矩形刀版（直插盒/自锁底展开等主要几何形态）；斜角、曲线与非直线轮廓不在本方法能力内，如实申报而非沉默",
    "cut/fold 线型由实测沿线覆盖率与缺口判定，是几何启发式（line_basis=geometric-heuristic），不等同工艺语义鉴定",
    "未使用任何 CV/ML 模型：阈值、线宽、覆盖率、连通域面积、外轮廓长全部可由本响应逐项复算",
    "墨迹中的块状内容（文字/图符/条码）计入 other_ink_px 并作为障碍参与连通域，故受影响面板的矩形度会如实下降",
    "同一线带上首尾相接的实线段与虚线段会共线合并为一条，其 kind 按合并后整体覆盖率判定，可能掩盖该线带内部的 cut/fold 切换；"
    "逐面板邻接的 separating_line 由相邻两面板间实际墨带覆盖率单独判定，为该边线型的权威口径",
]


class PanelRecognitionError(ValueError):
    """输入不可解析为位图刀版（非图像文件/尺寸非法等），由服务层转 400。"""


# ---------------------------------------------------------------------------
# 基础工具：阈值、游程、连通域
# ---------------------------------------------------------------------------

def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu 类间方差最大化，返回 0..255 的阈值（整数，可回显复算）。"""
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = float(gray.size)
    if total <= 0 or hist.sum() <= 0:
        return 127
    omega = np.cumsum(hist) / total
    t = np.arange(256, dtype=np.float64)
    mu = np.cumsum(hist * t)
    mu_t = mu / np.maximum(omega, 1e-12)
    mu_b = np.where(omega > 0, (mu[-1] - mu) / np.maximum(1.0 - omega, 1e-12), 0.0)
    w0 = omega
    sigma_b = np.maximum(w0 * (1.0 - w0), 0.0) * (mu_t - mu_b) ** 2
    return int(np.nanargmax(sigma_b))


def row_runs(mask: np.ndarray) -> List[Tuple[int, int, int]]:
    """布尔矩阵 → [(row, col_start, col_end_inclusive), ...]（按行、行内按列升序）。"""
    out: List[Tuple[int, int, int]] = []
    for y in range(mask.shape[0]):
        for x1, x2 in _runs_of_row(mask[y]):
            out.append((y, x1, x2))
    return out


def _runs_of_row(row: np.ndarray) -> List[Tuple[int, int]]:
    if not row.any():
        return []
    v = row.astype(np.int8)
    d = np.diff(np.concatenate(([0], v, [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1) - 1
    return list(zip(starts.tolist(), ends.tolist()))


def axis_run_extent(mask: np.ndarray, axis: int) -> np.ndarray:
    """每个 True 像素沿 axis 方向所在连续 True 段的长度（False 处为 0）。"""
    if axis == 0:  # 垂直方向（跨行）
        return _v_extent(mask)
    return _v_extent(mask.T).T


def _v_extent(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    up = np.zeros((h, w), dtype=np.int32)
    down = np.zeros((h, w), dtype=np.int32)
    zeros = np.zeros(w, dtype=np.int32)
    for y in range(h):
        prev = up[y - 1] if y > 0 else zeros
        up[y] = np.where(mask[y], prev + 1, 0).astype(np.int32)
    for y in range(h - 1, -1, -1):
        nxt = down[y + 1] if y < h - 1 else zeros
        down[y] = np.where(mask[y], nxt + 1, 0).astype(np.int32)
    ext = np.where(mask, up + down - 1, 0).astype(np.int32)
    return ext


def label_components(mask: np.ndarray) -> List[Dict[str, Any]]:
    """游程并查集 4-邻接连通域：返回 [{area_px,y1,y2,x1,x2,touches_border}]，按 (-area,y1,x1) 排序。"""
    h, w = mask.shape
    parent: List[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    runs: List[Tuple[int, int, int, int]] = []  # (y, x1, x2, id)
    comps: List[Dict[str, Any]] = []
    prev: List[Tuple[int, int, int]] = []
    for y in range(h):
        cur: List[Tuple[int, int, int]] = []
        for x1, x2 in _runs_of_row(mask[y]):
            rid = len(parent)
            parent.append(rid)
            runs.append((y, x1, x2, rid))
            for px1, px2, pid in prev:
                if px2 >= x1 and x2 >= px1:
                    union(rid, pid)
            cur.append((x1, x2, rid))
        prev = cur

    agg: Dict[int, Dict[str, Any]] = {}
    for y, x1, x2, rid in runs:
        root = find(rid)
        a = agg.get(root)
        if a is None:
            agg[root] = {"area_px": x2 - x1 + 1, "y1": y, "y2": y, "x1": x1, "x2": x2}
        else:
            a["area_px"] += x2 - x1 + 1
            a["y1"] = min(a["y1"], y)
            a["y2"] = max(a["y2"], y)
            a["x1"] = min(a["x1"], x1)
            a["x2"] = max(a["x2"], x2)
    out: List[Dict[str, Any]] = []
    for a in agg.values():
        a["touches_border"] = bool(a["y1"] == 0 or a["x1"] == 0
                                   or a["y2"] == h - 1 or a["x2"] == w - 1)
        out.append(a)
    out.sort(key=lambda d: (-d["area_px"], d["y1"], d["x1"]))
    return out


def collinear_segments(runs: Sequence[Tuple[int, int]], axis: int,
                       tol: int, max_gap: int) -> List[Dict[str, Any]]:
    """把同一条线带上的短段（实线段或虚线的每一划）按间隙合并为段。

    axis=0：输入为 [(row, col_start, col_end)] 的水平线短段，沿列方向合并。
    axis=1：输入为 [(col, row_start, row_end)] 的垂直线短段，沿行方向合并。
    """
    order = sorted(runs, key=lambda t: (t[0], t[1]))
    groups: List[Dict[str, Any]] = []
    for band, s1, s2 in order:
        hit = None
        for g in groups:
            if abs(band - g["band"]) <= tol and s1 <= g["end"] + max_gap and s2 >= g["start"] - max_gap:
                hit = g
                break
            if abs(band - g["band"]) <= tol and s1 >= g["start"] and s1 <= g["end"] + max_gap:
                hit = g
                break
        if hit is None:
            groups.append({"band": band, "bands": [band], "start": s1, "end": s2,
                           "parts": [(s1, s2)]})
        else:
            hit["parts"].append((s1, s2))
            hit["bands"].append(band)
            hit["start"] = min(hit["start"], s1)
            hit["end"] = max(hit["end"], s2)
    segs: List[Dict[str, Any]] = []
    for g in groups:
        parts = sorted(g["parts"])
        merged: List[Tuple[int, int]] = []
        for a, b in parts:
            if merged and a <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        span = g["end"] - g["start"] + 1
        covered = sum(b - a + 1 for a, b in merged)
        segs.append({
            "axis": "h" if axis == 0 else "v",
            "band": min(g["bands"]),
            "band_max": max(g["bands"]),
            "start": g["start"], "end": g["end"],
            "extent_px": span,
            "covered_px": covered,
            "dash_count": len(merged),
            "coverage": round(covered / span, 4) if span else 0.0,
        })
    segs.sort(key=lambda s: (0 if s["axis"] == "h" else 1, s["band"], s["start"]))
    return segs


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class PanelRecognitionEngine:
    """栅格刀版 → 面板识别（确定性几何，无 CV/ML 依赖）。"""

    def __init__(self, solid_coverage_threshold: float = 0.95,
                 rectangularity_threshold: float = 0.98) -> None:
        self.solid_coverage = float(solid_coverage_threshold)
        self.rect_threshold = float(rectangularity_threshold)

    # ---- 主入口 ----
    def recognize(self, image_path: str | Path, *, min_panel_area: int = 64,
                  max_line_width: int = 12, min_line_length: Optional[int] = None,
                  dash_merge_gap: Optional[int] = None,
                  px_per_mm: Optional[float] = None) -> Dict[str, Any]:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"刀版图像不存在: {path}")
        gray, orig_mode, size = self._load_gray(path)
        h, w = gray.shape
        if h < 3 or w < 3:
            raise PanelRecognitionError(f"图像尺寸过小，无法做几何反解: {w}x{h}")

        thr = otsu_threshold(gray)
        ink = gray <= thr
        ink_px = int(ink.sum())

        min_len = int(min_line_length) if min_line_length else max(8, min(h, w) // 20)
        max_lw = int(max_line_width)
        if max_lw < 1:
            raise PanelRecognitionError(f"max_line_width 必须为正整数: {max_lw}")
        if min_len < 2:
            raise PanelRecognitionError(f"min_line_length 至少为 2: {min_len}")
        if int(min_panel_area) < 1:
            raise PanelRecognitionError(f"min_panel_area 必须为正整数: {min_panel_area}")
        if px_per_mm is not None:
            k = float(px_per_mm)
            if not (k > 0) or k != k or k == float("inf"):
                raise PanelRecognitionError(f"px_per_mm 必须为正实数（像素/毫米比例）: {px_per_mm}")

        vert_ext = axis_run_extent(ink, 0)   # 每像素所在垂直墨段的长度
        horiz_ext = axis_run_extent(ink, 1)  # 每像素所在水平墨段的长度
        hline = ink & (horiz_ext >= min_len) & (vert_ext <= max_lw)
        vline = ink & (vert_ext >= min_len) & (horiz_ext <= max_lw)
        line_mask = hline | vline
        line_px = int(line_mask.sum())
        other_ink_px = max(0, ink_px - line_px)

        gap = int(dash_merge_gap) if dash_merge_gap else max(4, max_lw)
        h_runs = row_runs(hline)
        v_runs = self._vertical_runs(vline)
        h_segs = collinear_segments(h_runs, 0, max(1, max_lw // 2), gap)
        v_segs = collinear_segments(v_runs, 1, max(1, max_lw // 2), gap)
        for s in h_segs + v_segs:
            s["kind"] = "cut" if s["coverage"] >= self.solid_coverage else "fold"
            s["line_basis"] = LINE_BASIS
        self._complete_support(ink, h_segs, v_segs)

        result: Dict[str, Any] = {
            "engine": ENGINE_NAME, "implemented": True, "fabricated": False,
            "status": "recognized",
            "method": METHOD,
            "image": {"path": str(path), "width_px": int(w), "height_px": int(h),
                      "source_mode": orig_mode},
            "binarize": {"method": "otsu", "threshold": int(thr),
                         "ink_px": ink_px, "line_px": line_px,
                         "other_ink_px": other_ink_px},
            "params": {"min_panel_area": int(min_panel_area),
                       "max_line_width": max_lw, "min_line_length": int(min_len),
                       "dash_merge_gap": int(gap),
                       "solid_coverage_threshold": self.solid_coverage,
                       "rectangularity_threshold": self.rect_threshold},
            "lines": self._line_summary(h_segs, v_segs),
            "caveats": list(CAVEATS),
            "reason": None,
        }
        if px_per_mm is not None:
            result["units"] = {"px_per_mm": float(px_per_mm)}

        if line_px == 0 or (not h_segs and not v_segs):
            # 响应面一致性：降级分支与正常分支字段集完全相同（消费方无需按 status 分支取值）
            empty_barrier = self._barrier_mask(line_mask, h_segs, v_segs)
            result["status"] = "no_lines_detected"
            result["panels"] = []
            result["panel_count"] = 0
            result["rejected"] = []
            result["rejected_count"] = 0
            result["background"] = {"component_count": 0, "largest_area_px": 0}
            result["segments"] = {"horizontal": [], "vertical": []}
            result["topology"] = self._empty_topology()
            result["outline"] = self._outline([])
            result["binarize"]["barrier_px"] = int(empty_barrier.sum())
            result["binarize"]["blocked_px"] = int((empty_barrier | ink).sum())
            result["reason"] = ("二值化后未检出任何达到长度门限的轴对齐线段，"
                                "故无可报告的面板（不臆造型）")
            return result

        # 屏障 = 线像素 ∪ 合并段支撑（虚线 dash 缺口按几何支撑补齐，否则虚线在拓扑上
        # 不封闭、面板会连成一片；线型判定仍用真实墨覆盖率，见 _strip_kind）。
        # 空白 = 非(屏障 ∪ 任何墨)：十字节点与块状墨迹虽不被判为线，也绝不能算纸面，
        # 否则节点自身会变成面板之间的漏点。
        barrier = self._barrier_mask(line_mask, h_segs, v_segs)
        blocked = barrier | ink
        free = ~blocked
        result["binarize"]["barrier_px"] = int(barrier.sum())
        result["binarize"]["blocked_px"] = int(blocked.sum())
        comps = label_components(free)
        background = [c for c in comps if c["touches_border"]]
        enclosed = [c for c in comps if not c["touches_border"]]
        panels: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for c in enclosed:
            bw, bh = c["x2"] - c["x1"] + 1, c["y2"] - c["y1"] + 1
            bbox_area = bw * bh
            rect = round(c["area_px"] / bbox_area, 4) if bbox_area else 0.0
            if c["area_px"] < min_panel_area:
                rejected.append({"bbox": [c["x1"], c["y1"], c["x2"], c["y2"]],
                                 "area_px": c["area_px"],
                                 "reason": f"面积小于 min_panel_area={min_panel_area}，判为噪声/内部孔洞而非面板"})
                continue
            p: Dict[str, Any] = {
                "id": f"panel-{len(panels) + 1}",
                "bbox_px": [c["x1"], c["y1"], c["x2"], c["y2"]],
                "x": int(c["x1"]), "y": int(c["y1"]),
                "width_px": int(bw), "height_px": int(bh),
                "area_px": int(c["area_px"]), "bbox_area_px": int(bbox_area),
                "rectangularity": rect,
                "rectangular": bool(rect >= self.rect_threshold),
                "interior_obstacle_px": int(bbox_area - c["area_px"]),
            }
            if px_per_mm is not None:
                k = float(px_per_mm)
                p["width_mm"] = round(bw / k, 3)
                p["height_mm"] = round(bh / k, 3)
            panels.append(p)

        result["background"] = {"component_count": len(background),
                                "largest_area_px": int(max((c["area_px"] for c in background), default=0))}
        result["rejected"] = rejected
        result["rejected_count"] = len(rejected)
        result["panels"] = panels
        result["panel_count"] = len(panels)
        result["segments"] = {"horizontal": h_segs, "vertical": v_segs}
        result["topology"] = self._topology(panels, ink, max_lw)
        result["outline"] = self._outline(panels)
        if not panels:
            result["status"] = "no_panels_detected"
            result["reason"] = ("检出线段但无满足 min_panel_area 的封闭区域：线未闭合或"
                                "全部区域被判为噪声（不臆造型）")
        return result

    # ---- 内部 ----
    @staticmethod
    def _load_gray(path: Path) -> Tuple[np.ndarray, str, Tuple[int, int]]:
        if not PIL_AVAILABLE:
            raise PanelRecognitionError("Pillow 不可用，无法读取位图刀版")
        try:
            with Image.open(path) as im:
                mode = im.mode
                if mode in ("RGBA", "LA") or "transparency" in im.info:
                    im = im.convert("RGBA")
                    base = Image.new("RGB", im.size, (255, 255, 255))
                    base.paste(im, mask=im.split()[-1])
                    im = base
                gray = np.asarray(im.convert("L"))
        except PanelRecognitionError:
            raise
        except Exception as e:
            raise PanelRecognitionError(
                f"文件无法作为位图图像解码: {type(e).__name__}: {e}") from e
        return gray, mode, (int(gray.shape[1]), int(gray.shape[0]))

    @staticmethod
    def _complete_support(stop_mask: np.ndarray, h_segs: List[Dict[str, Any]],
                          v_segs: List[Dict[str, Any]]) -> None:
        """把每段的**几何支撑**沿本线带向两侧延伸到下一处墨迹为止。

        虚线常以"最后一划停在边界前"的形态出货，若只按墨迹建屏障，面板会从该缺口
        连通成更少的面板。终止条件取"任何墨像素"而非"已判为线的像素"：十字节点的
        墨同时超线宽与线长门限，既不属水平线也不属垂直线，只认线像素会让支撑穿过
        节点、把两条独立的边桥接成一条。到画幅边缘仍无墨则不补（绝不把开放端拉通）。
        覆盖率与 dash 数仍按原始墨迹统计，故线型判定不受补齐影响（可复算）。
        """
        h, w = stop_mask.shape
        for s in h_segs:
            rows = slice(s["band"], s["band_max"] + 1)
            x = s["start"] - 1
            while x >= 0 and not stop_mask[rows, x].any():
                x -= 1
            s["support_from_px"] = int(x + 1) if x >= 0 else int(s["start"])
            x = s["end"] + 1
            while x < w and not stop_mask[rows, x].any():
                x += 1
            s["support_to_px"] = int(x - 1) if x < w else int(s["end"])
        for s in v_segs:
            cols = slice(s["band"], s["band_max"] + 1)
            y = s["start"] - 1
            while y >= 0 and not stop_mask[y, cols].any():
                y -= 1
            s["support_from_px"] = int(y + 1) if y >= 0 else int(s["start"])
            y = s["end"] + 1
            while y < h and not stop_mask[y, cols].any():
                y += 1
            s["support_to_px"] = int(y - 1) if y < h else int(s["end"])

    @staticmethod
    def _barrier_mask(line_mask: np.ndarray, h_segs: List[Dict[str, Any]],
                      v_segs: List[Dict[str, Any]]) -> np.ndarray:
        barrier = line_mask.copy()
        for s in h_segs:
            barrier[s["band"]:s["band_max"] + 1,
                    s["support_from_px"]:s["support_to_px"] + 1] = True
        for s in v_segs:
            barrier[s["support_from_px"]:s["support_to_px"] + 1,
                    s["band"]:s["band_max"] + 1] = True
        return barrier

    @staticmethod
    def _vertical_runs(vline: np.ndarray) -> List[Tuple[int, int, int]]:
        """垂直线短段 → [(col, row_start, row_end)]，按 (col, row_start) 升序。"""
        out: List[Tuple[int, int, int]] = []
        for x, y1, y2 in row_runs(vline.T.copy()):
            out.append((int(x), int(y1), int(y2)))
        out.sort(key=lambda t: (t[0], t[1]))
        return out

    def _line_summary(self, h_segs: List[Dict[str, Any]],
                      v_segs: List[Dict[str, Any]]) -> Dict[str, Any]:
        allsegs = h_segs + v_segs
        cut = [s for s in allsegs if s["kind"] == "cut"]
        fold = [s for s in allsegs if s["kind"] == "fold"]

        def agg(items: List[Dict[str, Any]]) -> Dict[str, Any]:
            return {"segments": len(items),
                    "length_px": int(sum(i["extent_px"] for i in items)),
                    "covered_px": int(sum(i["covered_px"] for i in items)),
                    "dashes": int(sum(i["dash_count"] for i in items))}
        return {"cut": agg(cut), "fold": agg(fold),
                "basis": LINE_BASIS,
                "rule": f"沿线覆盖率 >= {self.solid_coverage} 判 cut（实线），出现周期性缺口判 fold（虚线/齿孔）"}

    @staticmethod
    def _empty_topology() -> Dict[str, Any]:
        return {"adjacency_count": 0, "adjacencies": [], "degrees": {},
                "degree_sequence": [], "max_degree": 0, "connected_groups": 0,
                "is_single_connected_net": False}

    def _topology(self, panels: List[Dict[str, Any]], ink_mask: np.ndarray,
                  max_line_width: int) -> Dict[str, Any]:
        adj: List[Dict[str, Any]] = []
        edges: Dict[str, List[str]] = {p["id"]: [] for p in panels}
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                a, b = panels[i], panels[j]
                rel = self._shared_edge(a, b, ink_mask, max_line_width)
                if rel is None:
                    continue
                adj.append({"a": a["id"], "b": b["id"], **rel})
                edges[a["id"]].append(b["id"])
                edges[b["id"]].append(a["id"])
        degrees = {k: len(v) for k, v in edges.items()}
        seq = sorted(degrees.values(), reverse=True)
        seen: set = set()
        groups = 0
        for pid in edges:
            if pid in seen:
                continue
            groups += 1
            q = deque([pid])
            seen.add(pid)
            while q:
                cur = q.popleft()
                for nxt in edges[cur]:
                    if nxt not in seen:
                        seen.add(nxt)
                        q.append(nxt)
        return {"adjacency_count": len(adj), "adjacencies": adj,
                "degrees": degrees, "degree_sequence": seq,
                "max_degree": int(max(seq, default=0)),
                "connected_groups": groups,
                "is_single_connected_net": bool(groups == 1 and len(panels) > 0)}

    def _shared_edge(self, a: Dict[str, Any], b: Dict[str, Any],
                     ink_mask: np.ndarray, max_line_width: int) -> Optional[Dict[str, Any]]:
        ax1, ay1, ax2, ay2 = a["bbox_px"]
        bx1, by1, bx2, by2 = b["bbox_px"]
        tol = max(0, max_line_width)
        # a 在左、b 在右
        for first, second, orient in ((ax2, bx1, "v"), (bx2, ax1, "v")):
            if orient == "v":
                gap = second - first - 1
                if 0 <= gap <= tol:
                    lo, hi = max(ay1, by1), min(ay2, by2)
                    if hi - lo + 1 >= 1:
                        x = first + 1
                        kind, cov = self._strip_kind(ink_mask, lo, hi, x, x + gap - 1, "v")
                        return {"orientation": "vertical-shared-edge",
                                "boundary_x_px": int(x), "span_from_px": int(lo),
                                "span_to_px": int(hi), "span_px": int(hi - lo + 1),
                                "line_gap_px": int(gap), "separating_line": kind,
                                "separating_coverage": cov, "line_basis": LINE_BASIS}
        for first, second in ((ay2, by1), (by2, ay1)):
            gap = second - first - 1
            if 0 <= gap <= tol:
                lo, hi = max(ax1, bx1), min(ax2, bx2)
                if hi - lo + 1 >= 1:
                    y = first + 1
                    kind, cov = self._strip_kind(ink_mask, lo, hi, y, y + gap - 1, "h")
                    return {"orientation": "horizontal-shared-edge",
                            "boundary_y_px": int(y), "span_from_px": int(lo),
                            "span_to_px": int(hi), "span_px": int(hi - lo + 1),
                            "line_gap_px": int(gap), "separating_line": kind,
                            "separating_coverage": cov, "line_basis": LINE_BASIS}
        return None

    @staticmethod
    def _strip_kind(ink_mask: np.ndarray, lo: int, hi: int,
                    s0: int, s1: int, orient: str) -> Tuple[str, float]:
        sub = ink_mask[lo:hi + 1, s0:s1 + 1] if orient == "v" else ink_mask[s0:s1 + 1, lo:hi + 1]
        n = sub.size
        cov = round(float(sub.sum()) / n, 4) if n else 0.0
        if cov >= 0.95:
            return ("cut", cov)
        if cov <= 0.05:
            return ("absent", cov)
        return ("fold", cov)

    @staticmethod
    def _outline(panels: List[Dict[str, Any]]) -> Dict[str, Any]:
        """外轮廓长 = 面板占用并集的边界长（2×每行游程数 + 2×每列游程数）。"""
        if not panels:
            return {"union_area_px": 0, "bbox": None, "perimeter_px": 0,
                    "row_runs": 0, "col_runs": 0}
        xs0 = min(p["x"] for p in panels)
        ys0 = min(p["y"] for p in panels)
        xs1 = max(p["x"] + p["width_px"] - 1 for p in panels)
        ys1 = max(p["y"] + p["height_px"] - 1 for p in panels)
        occ = np.zeros((ys1 - ys0 + 1, xs1 - xs0 + 1), dtype=bool)
        for p in panels:
            occ[p["y"] - ys0:p["y"] + p["height_px"] - ys0,
                p["x"] - xs0:p["x"] + p["width_px"] - xs0] = True
        row_runs_total = 0
        for y in range(occ.shape[0]):
            row_runs_total += len(_runs_of_row(occ[y]))
        col_runs_total = 0
        for x in range(occ.shape[1]):
            col_runs_total += len(_runs_of_row(occ[:, x]))
        return {"union_area_px": int(occ.sum()),
                "bbox": [int(xs0), int(ys0), int(xs1), int(ys1)],
                "perimeter_px": int(2 * row_runs_total + 2 * col_runs_total),
                "row_runs": int(row_runs_total), "col_runs": int(col_runs_total)}


def recognize_panels(image_path: str | Path, **kw: Any) -> Dict[str, Any]:
    return PanelRecognitionEngine().recognize(image_path, **kw)


# ---------------------------------------------------------------------------
# 自供真值夹具 + 自测（L1 扫描对象；全部期望为手算或结构性可复算）
# ---------------------------------------------------------------------------

def write_fixture(directory: Path, name: str) -> Path:
    """在 directory 下按已知像素坐标自绘刀版夹具（真值由此可手算）。

    坐标刻意选成"stroke 1px + 内区行列数为奇数"，使折线恰落在正中行/列，
    从而两侧面板像素数完全相等 —— 期望值可用手算式 2*(宽+高) 直接核对。
    """
    from PIL import Image, ImageDraw

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / name
    if name == "pr_blank_white.png":
        Image.new("RGB", (200, 160), (255, 255, 255)).save(out)
        return out
    if name == "pr_grid2x2.png":
        # 外裁切框 1px：stroke 占 row 10 / row 150 / col 10 / col 190
        # → 内区 rows 11..149 (139) x cols 11..189 (179)
        # 折线：row 80 水平虚线（上下各 69 行）、col 100 垂直虚线（左右各 89 列）
        im = Image.new("RGB", (200, 160), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([10, 10, 190, 150], outline=(0, 0, 0), width=1)
        _dash(d, "h", 80, 11, 189)
        _dash(d, "v", 100, 11, 149)
        im.save(out)
        return out
    if name == "pr_disjoint.png":
        # 两枚互不相交的封闭刀版（2px stroke）：内区 97x77 与 97x87
        im = Image.new("RGB", (300, 120), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([10, 10, 90, 110], outline=(0, 0, 0), width=2)
        d.rectangle([200, 10, 290, 110], outline=(0, 0, 0), width=2)
        im.save(out)
        return out
    if name == "pr_tube_3panel.png":
        # 三面管式盒：外裁切框 + 两条垂直折线 → 3 面板，度序列 [2,1,1]
        im = Image.new("RGB", (220, 100), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([10, 20, 210, 80], outline=(0, 0, 0), width=1)
        _dash(d, "v", 69, 21, 79)
        _dash(d, "v", 140, 21, 79)
        im.save(out)
        return out
    if name == "pr_straight_tuck.png":
        # 真实直插盒（Straight Tuck End）十字展开：糊口 + 4 面身 + 上下各 2 插盖 = 9 面板
        # 身行 y 40..120，糊口 x 10..25，四侧板分界 x=25/85/125/185（虚折线）
        im = Image.new("RGB", (240, 160), (255, 255, 255))
        d = ImageDraw.Draw(im)
        black = (0, 0, 0)
        d.line([10, 40, 10, 120], fill=black)          # 左边裁切
        d.line([225, 40, 225, 120], fill=black)        # 右边裁切
        for x0, x1 in ((10, 25), (85, 125), (185, 225)):
            d.line([x0, 40, x1, 40], fill=black)       # 上边裁切（无插盖处）
            d.line([x0, 120, x1, 120], fill=black)     # 下边裁切
        for x in (25, 85, 125, 185):
            _dash(d, "v", x, 40, 120)                  # 四条竖折线
        for x0, x1 in ((25, 85), (125, 185)):
            _dash(d, "h", 40, x0, x1)                  # 上盖折线
            _dash(d, "h", 120, x0, x1)                 # 下盖折线
            d.line([x0, 20, x1, 20], fill=black)       # 上盖顶裁切
            d.line([x0, 140, x1, 140], fill=black)     # 上盖顶裁切
            d.line([x0, 20, x0, 40], fill=black)
            d.line([x1, 20, x1, 40], fill=black)
            d.line([x0, 120, x0, 140], fill=black)
            d.line([x1, 120, x1, 140], fill=black)
        im.save(out)
        return out
    if name == "pr_solid_split.png":
        # 外裁切框 + 一条**实线**垂直分界 → 2 面板，邻边权威口径必须判 cut
        im = Image.new("RGB", (200, 100), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([10, 10, 189, 89], outline=(0, 0, 0), width=1)
        d.line([99, 11, 99, 88], fill=(0, 0, 0))
        im.save(out)
        return out
    if name == "pr_open_cross.png":
        # 一横一竖两条虚线相交、**无外裁切框** → 有段无封闭域（no_panels_detected 反零向量）
        im = Image.new("RGB", (220, 120), (255, 255, 255))
        d = ImageDraw.Draw(im)
        _dash(d, "h", 60, 6, 211)
        _dash(d, "v", 110, 6, 113)
        im.save(out)
        return out
    raise AssertionError(f"未知夹具: {name}")


def _dash(draw, axis: str, pos: int, lo: int, hi: int,
          dash: int = 10, gap: int = 4) -> None:
    """在 axis="h" 的第 pos 行（列 lo..hi）或 axis="v" 的第 pos 列（行 lo..hi）画虚线：
    每段 dash 像素长划 + gap 像素空档，逐像素确定可复算。"""
    c = lo
    while c <= hi:
        end = min(c + dash - 1, hi)
        if axis == "h":
            draw.line([c, pos, end, pos], fill=(0, 0, 0), width=1)
        else:
            draw.line([pos, c, pos, end], fill=(0, 0, 0), width=1)
        c = end + 1 + gap


def _selftest() -> int:
    import tempfile
    eng = PanelRecognitionEngine()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1) 空白图：无墨无线 → 如实 no_lines_detected，绝不臆造面板
        blank = write_fixture(tmp, "pr_blank_white.png")
        r = eng.recognize(blank)
        assert r["status"] == "no_lines_detected", r["status"]
        assert r["panel_count"] == 0 and r["panels"] == []
        assert r["implemented"] is True and r["fabricated"] is False
        assert r["binarize"]["line_px"] == 0, r["binarize"]

        # 1b) 响应面一致性：三种 status 的字段集完全相同（消费方无需按 status 分支取值）
        cross = eng.recognize(write_fixture(tmp, "pr_open_cross.png"))
        assert cross["status"] == "no_panels_detected", cross["status"]
        assert cross["panel_count"] == 0 and cross["lines"]["fold"]["segments"] == 2, cross["lines"]
        ok = eng.recognize(write_fixture(tmp, "pr_grid2x2.png"))
        assert sorted(ok) == sorted(r) == sorted(cross), (
            f"{set(ok) - set(r)} {set(r) - set(ok)} {set(ok) - set(cross)} {set(cross) - set(ok)}")
        for sub in ("topology", "outline", "binarize", "background", "lines", "params", "segments"):
            assert sorted(ok[sub]) == sorted(r[sub]) == sorted(cross[sub]), sub

        # 2) 2x2 网格：4 面板全等、两折四裁、外轮廓 perimeter 手算 1264
        grid = write_fixture(tmp, "pr_grid2x2.png")
        g = eng.recognize(grid)
        assert g["status"] == "recognized" and g["panel_count"] == 4, g["panel_count"]
        areas = sorted(p["area_px"] for p in g["panels"])
        assert areas == [69 * 89] * 4, areas
        assert all(p["rectangular"] for p in g["panels"]), g["panels"]
        assert min(p["x"] for p in g["panels"]) == 11, g["panels"]
        assert g["topology"]["connected_groups"] == 1
        assert g["topology"]["adjacency_count"] == 4, g["topology"]["adjacency_count"]
        assert g["topology"]["degree_sequence"] == [2, 2, 2, 2], g["topology"]
        assert g["lines"]["fold"]["segments"] == 2, g["lines"]
        assert g["lines"]["cut"]["segments"] == 4, g["lines"]
        assert g["lines"]["fold"]["dashes"] == 13 + 10, g["lines"]["fold"]
        kinds = {a["separating_line"] for a in g["topology"]["adjacencies"]}
        assert kinds == {"fold"}, kinds
        # 4 枚 69x89 面板互不重叠 → 并集边界长 = 4 * 2*(69+89) = 1264
        assert g["outline"]["perimeter_px"] == 1264, g["outline"]

        # 2b) 实线分界必须按邻边权威口径判 cut（抽样带=两面板 bbox 之间的 gap 列，
        #     不得把对侧面板自身首列算进分母；修复前该边被稀释成 cov≈0.5 的假 fold）
        sp = eng.recognize(write_fixture(tmp, "pr_solid_split.png"))
        assert sp["status"] == "recognized" and sp["panel_count"] == 2, sp["status"]
        assert sorted(p["area_px"] for p in sp["panels"]) == [6864, 6942], sp["panels"]
        assert sp["topology"]["adjacency_count"] == 1, sp["topology"]
        _spa = sp["topology"]["adjacencies"][0]
        assert (_spa["separating_line"], _spa["separating_coverage"],
                _spa["line_gap_px"], _spa["boundary_x_px"]) == ("cut", 1.0, 1, 99), _spa

        # 3) 三面板管式盒：2 条折线分隔、度序列 [1,2,1]
        tube = write_fixture(tmp, "pr_tube_3panel.png")
        t = eng.recognize(tube)
        assert t["panel_count"] == 3, t["panel_count"]
        assert t["topology"]["degree_sequence"] == [2, 1, 1], t["topology"]
        assert t["lines"]["fold"]["segments"] == 2, t["lines"]

        # 4) 两个不相交刀版：connected_groups == 2，非单一连通，外轮廓两段独立
        dis = write_fixture(tmp, "pr_disjoint.png")
        dd = eng.recognize(dis)
        assert dd["panel_count"] == 2, dd["panel_count"]
        assert dd["topology"]["connected_groups"] == 2, dd["topology"]
        assert dd["topology"]["is_single_connected_net"] is False
        assert dd["topology"]["adjacency_count"] == 0, dd["topology"]
        # 2px stroke 向内占 2 行/列 → 内区 97x77 与 97x87，并集边界 = 348 + 368
        assert [p["area_px"] for p in sorted(dd["panels"], key=lambda p: p["x"])] == \
            [97 * 77, 97 * 87], dd["panels"]
        assert dd["outline"]["perimeter_px"] == 2 * (97 + 77) + 2 * (97 + 87), dd["outline"]

        # 8) 真实直插盒十字展开：9 面板、单一连通、8 条邻边、全部为折线分隔
        net = write_fixture(tmp, "pr_straight_tuck.png")
        nn = eng.recognize(net)
        assert nn["panel_count"] == 9, nn["panel_count"]
        assert nn["topology"]["connected_groups"] == 1, nn["topology"]
        assert nn["topology"]["is_single_connected_net"] is True
        assert nn["topology"]["adjacency_count"] == 8, nn["topology"]["adjacency_count"]
        # 手算度序列：糊口 1 / 前 4 / 侧 2 / 后 4 / 左 1 / 四盖各 1 → [4,4,2,1,1,1,1,1,1]
        assert nn["topology"]["degree_sequence"] == [4, 4, 2, 1, 1, 1, 1, 1, 1], \
            nn["topology"]["degree_sequence"]
        assert {a["separating_line"] for a in nn["topology"]["adjacencies"]} == {"fold"}
        areas = sorted(p["area_px"] for p in nn["panels"])
        assert areas[0] == 14 * 79, areas  # 糊口：x 11..24 × y 41..119
        assert nn["rejected_count"] == 0, nn["rejected"]

        # 5) 确定性：同图两次调用逐字段全等；幽灵输入中文抛错
        a = eng.recognize(grid)
        b = eng.recognize(grid)
        assert a == b, "识别结果不确定（同输入两次不等）"
        try:
            eng.recognize(tmp / "ghost_dieline.png")
        except FileNotFoundError as e:
            assert "刀版图像不存在" in str(e), str(e)
        else:
            raise AssertionError("幽灵路径应抛 FileNotFoundError")
        # 6) 非图像：解码失败 → PanelRecognitionError，不抛崩
        junk = tmp / "junk.txt"
        junk.write_text("not an image", encoding="utf-8")
        try:
            eng.recognize(junk)
        except PanelRecognitionError as e:
            assert "无法作为位图图像解码" in str(e), str(e)
        else:
            raise AssertionError("非图像应抛 PanelRecognitionError")
        # 7) 敌意入参：四类非法门限一律 PanelRecognitionError（服务层转 400，绝不 500）
        for kw in ({"max_line_width": 0}, {"min_line_length": 1}, {"min_panel_area": 0},
                   {"px_per_mm": 0}, {"px_per_mm": -2.5}, {"px_per_mm": float("nan")}):
            try:
                eng.recognize(grid, **kw)
            except PanelRecognitionError:
                continue
            raise AssertionError(f"敌意入参应被拒绝: {kw}")
    print("[OK] panel_recognition_engine 自测全通过（8 组真值/降级/幂等）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
