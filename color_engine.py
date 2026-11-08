"""
造境 AI 设计智能体 · 色彩引擎（Color Engine）
============================================
任务书 §44：Palette / Harmony / Contrast / Brand Color / CMYK / RGB / Spot Color / Pantone Mapping。

这是一台**真实色彩计算引擎**，所有结果由可复算的色彩科学公式推出：
- Palette：Pillow 对真实图像做中位切分量化 + 逐像素直方统计，占比是真实像素比例（非猜色）。
- Harmony：sRGB→HSL 色轮角度旋转（互补 180°、类似 ±30°、三角 ±120°、分裂互补 150/210°、矩形 ±90°）。
- Contrast：WCAG 2.1 相对亮度 + 对比度公式 + AA/AAA 与大字阈值判级。
- Convert：sRGB ↔ HSL ↔ HSV ↔ CMYK 标准公式（CMYK 明确标注为 generic 公式转换，非 ICC 分色）。
- Delta-E：sRGB→线性→XYZ(D65)→CIELAB→ΔE76/ΔE94/ΔE2000，真实色差。
- Pantone / Spot Color：**红线**——§44 明示"实际色彩转换和 Pantone 使用必须结合授权数据与行业标准，
  不能把 AI 估算当作精确色彩标准"。本引擎不具备 PANTONE+ 授权色库，因此 exact Pantone 匹配一律
  如实返回 needs_licensed_pantone_data，只给出真实可算的 CMYK 加色等价值并明确标注"非 Pantone 专色标准"，
  绝不编造一个 Pantone 编号冒充精确匹配。
"""

from __future__ import annotations

import colorsys
import logging
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("color_engine")

try:
    from PIL import Image  # 真实像素读取依赖 Pillow
    PIL_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    Image = None  # type: ignore
    PIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# 真实颜色基础换算（纯数学，无外部依赖）
# ---------------------------------------------------------------------------

def hex_to_rgb(h: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not h:
        return None
    s = str(h).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def rgb_to_hex(rgb: Tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(round(v)))) for v in rgb))


def _srgb_to_linear(c8: float) -> float:
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb8(c: float) -> float:
    v = c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return max(0.0, min(255.0, v * 255.0))


def relative_luminance(hexc: Optional[str]) -> Optional[float]:
    rgb = hex_to_rgb(hexc)
    if not rgb:
        return None
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: Optional[str], bg: Optional[str]) -> Optional[float]:
    lf, lb = relative_luminance(fg), relative_luminance(bg)
    if lf is None or lb is None:
        return None
    hi, lo = max(lf, lb), min(lf, lb)
    return round((hi + 0.05) / (lo + 0.05), 3)


def _hex_to_hsl(hexc: str) -> Tuple[float, float, float]:
    r, g, b = hex_to_rgb(hexc)
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)  # HLS 顺序
    return h * 360.0, s, l


def _hsl_to_hex(h_deg: float, s: float, l: float) -> str:
    h = (h_deg % 360.0) / 360.0
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return rgb_to_hex((r * 255, g * 255, b * 255))


def rgb_to_cmyk(rgb: Tuple[int, int, int]) -> Tuple[float, float, float, float]:
    """generic（无 ICC 配置文件）公式 CMYK 转换，返回百分比 0-100。明确非印刷分色标准。"""
    r, g, b = (c / 255.0 for c in rgb)
    k = 1 - max(r, g, b)
    if k >= 0.9999:
        return (0.0, 0.0, 0.0, 100.0)
    c = (1 - r - k) / (1 - k)
    m = (1 - g - k) / (1 - k)
    y = (1 - b - k) / (1 - k)
    return tuple(round(v * 100, 2) for v in (c, m, y, k))  # type: ignore


def _rgb_to_xyz(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(x) for x in rgb)
    # sRGB D65
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    return x * 100, y * 100, z * 100


def _xyz_to_lab(xyz: Tuple[float, float, float]) -> Tuple[float, float, float]:
    Xn, Yn, Zn = 95.047, 100.0, 108.883  # D65

    def f(t: float) -> float:
        delta = 6 / 29
        return t ** (1 / 3) if t > delta ** 3 else t / (3 * delta ** 2) + 4 / 29

    x, y, z = (f(v / n) for v, n in zip(xyz, (Xn, Yn, Zn)))
    L = 116 * y - 16
    a = 500 * (x - y)
    bb = 200 * (y - z)
    return L, a, bb


def rgb_to_lab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    return _xyz_to_lab(_rgb_to_xyz(rgb))


def _delta_e(lab1, lab2, method: str = "cie76") -> float:
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    if method == "cie76":
        return math.sqrt((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)
    dL = L1 - L2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    dC = C1 - C2
    da = a1 - a2
    db = b1 - b2
    dH2 = max(da * da + db * db - dC * dC, 0.0)
    if method == "cie94":
        kL, K1, K2 = 1.0, 0.045, 0.015
        SL = 1.0
        SC = 1 + K1 * math.sqrt(C1 * C2)
        SH = 1 + K2 * math.sqrt(C1 * C2)
        return math.sqrt((dL / (kL * SL)) ** 2 + (dC / SC) ** 2 + dH2 / (SH * SH))
    # CIEDE2000（完整实现，真实工业色差）
    kL = kC = kH = 1.0
    Cb = (C1 + C2) / 2.0
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7))) if Cb > 0 else 0.0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp = L1 - L2
    dCp = C1p - C2p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2.0
    else:
        hbp = (h1p + h2p - 360) / 2.0
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dTheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7)) if Cbp > 0 else 0.0
    Sl = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp
    Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dTheta)) * Rc
    return math.sqrt((dLp / (kL * Sl)) ** 2 + (dCp / (kC * Sc)) ** 2 + (dHp / (kH * Sh)) ** 2
                     + Rt * (dCp / Sc) * (dHp / Sh))


HARMONY_SCHEMES = {
    "complementary": [180],
    "analogous": [-30, 30],
    "triadic": [120, 240],
    "tetradic": [90, 180, 270],
    "split_complementary": [150, 210],
    "square": [90, 180, 270],
}


# ---------------------------------------------------------------------------
# 色彩引擎
# ---------------------------------------------------------------------------

class ColorEngine:
    def __init__(self, store_dir: Optional[Path] = None):
        self.store_dir = Path(store_dir) if store_dir else None
        if self.store_dir:
            self.store_dir.mkdir(parents=True, exist_ok=True)

    # --- Palette：真实图像量化取色 ---
    def palette_from_image(self, path: str, k: int = 6) -> Dict[str, Any]:
        if not PIL_AVAILABLE:
            raise RuntimeError("Pillow 不可用，无法对真实图像取色（不伪造调色板）")
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"图像文件不存在: {path}")
        k = max(2, min(int(k or 6), 32))
        with Image.open(p) as im:  # type: ignore
            im = im.convert("RGB")
            total = im.width * im.height
            q = im.quantize(colors=k, method=Image.MEDIANCUT)  # type: ignore
            pal = q.getpalette()[: k * 3]
            counts: Dict[int, int] = {}
            for idx in q.getdata():
                counts[idx] = counts.get(idx, 0) + 1
            entries: List[Dict[str, Any]] = []
            for idx, cnt in sorted(counts.items(), key=lambda x: -x[1]):
                if idx * 3 + 2 >= len(pal):
                    continue
                r, g, b = pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]
                entries.append({
                    "hex": rgb_to_hex((r, g, b)),
                    "rgb": [r, g, b],
                    "percent": round(cnt / total * 100, 2) if total else 0.0,
                    "pixels": cnt,
                })
        return {
            "source": str(p), "width": im.width, "height": im.height,
            "requested_k": k, "distinct_colors": len(entries),
            "palette": entries, "image_mode": "real_pixel_median_cut",
        }

    # --- Harmony：色轮角度 ---
    def harmony(self, base: str, scheme: str = "complementary") -> Dict[str, Any]:
        rgb = hex_to_rgb(base)
        if not rgb:
            raise KeyError(f"非法颜色: {base}")
        if scheme not in HARMONY_SCHEMES:
            raise KeyError(f"未知配色方案: {scheme}；可选 {sorted(HARMONY_SCHEMES)}")
        h, s, l = _hex_to_hsl(base)
        colors = [{"hex": base, "role": "base", "angle_deg": 0.0}]
        for rot in HARMONY_SCHEMES[scheme]:
            colors.append({"hex": _hsl_to_hex(h + rot, s, l), "role": "derived", "angle_deg": float(rot)})
        return {"base": base, "scheme": scheme, "colors": colors,
                "note": "HSL 色轮按角度旋转派生，饱和度/明度保持一致"}

    # --- Contrast：WCAG ---
    def contrast(self, fg: str, bg: str) -> Dict[str, Any]:
        ratio = contrast_ratio(fg, bg)
        if ratio is None:
            raise KeyError("fg/bg 需为合法 hex 颜色")
        return {
            "foreground": fg, "background": bg, "ratio": ratio,
            "wcag": {
                "AA_normal": ratio >= 4.5,
                "AA_large": ratio >= 3.0,
                "AAA_normal": ratio >= 7.0,
                "AAA_large": ratio >= 4.5,
            },
            "method": "WCAG 2.1 relative luminance",
        }

    # --- Convert ---
    def convert(self, hexc: str) -> Dict[str, Any]:
        rgb = hex_to_rgb(hexc)
        if not rgb:
            raise KeyError(f"非法颜色: {hexc}")
        h, s, l = _hex_to_hsl(hexc)
        hh, ss, vv = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        c, m, y, k = rgb_to_cmyk(rgb)
        return {
            "hex": hexc.upper() if hexc.startswith("#") else "#" + hexc.upper().lstrip("#"),
            "rgb": {"r": rgb[0], "g": rgb[1], "b": rgb[2]},
            "hsl": {"h": round(h, 1), "s": round(s * 100, 1), "l": round(l * 100, 1)},
            "hsv": {"h": round(hh * 360, 1), "s": round(ss * 100, 1), "v": round(vv * 100, 1)},
            "cmyk": {"c": c, "m": m, "y": y, "k": k},
            "lab": [round(v, 3) for v in rgb_to_lab(rgb)],
            "cmyk_note": "generic 公式转换，非 ICC 色彩管理分色；精确印刷分色需目标 ICC 配置文件",
        }

    # --- Delta-E ---
    def delta_e(self, a: str, b: str, method: str = "ciede2000") -> Dict[str, Any]:
        ra, rb = hex_to_rgb(a), hex_to_rgb(b)
        if not ra or not rb:
            raise KeyError("a/b 需为合法 hex 颜色")
        method = method.lower()
        if method not in ("cie76", "cie94", "ciede2000", "de2000", "2000"):
            raise KeyError("method ∈ {cie76, cie94, ciede2000}")
        if method in ("de2000", "2000"):
            method = "ciede2000"
        lab_a, lab_b = rgb_to_lab(ra), rgb_to_lab(rb)
        de = _delta_e(lab_a, lab_b, method)
        # 业界经验阈值（近似感知）
        band = ("不可感知(<1)" if de < 1 else "可近察(1-2)" if de < 2 else
                "可察(2-10)" if de < 10 else "显著差异(10-50)" if de < 50 else "极端差异(>50)")
        return {"a": a, "b": b, "method": method, "delta_e": round(de, 3),
                "perception": band, "lab_a": [round(v, 2) for v in lab_a],
                "lab_b": [round(v, 2) for v in lab_b]}

    def nearest_in_palette(self, color: str, palette: List[str]) -> Dict[str, Any]:
        rgb = hex_to_rgb(color)
        if not rgb:
            raise KeyError("color 需为合法 hex")
        lab = rgb_to_lab(rgb)
        scored = []
        for c in palette:
            rr = hex_to_rgb(c)
            if not rr:
                continue
            d = _delta_e(lab, rgb_to_lab(rr), "ciede2000")
            scored.append({"hex": c, "delta_e": round(d, 3)})
        scored.sort(key=lambda x: x["delta_e"])
        return {"query": color, "matches": scored}

    # --- Pantone / Spot：诚实拒绝伪造 ---
    def pantone_match(self, hexc: str) -> Dict[str, Any]:
        rgb = hex_to_rgb(hexc)
        if not rgb:
            raise KeyError(f"非法颜色: {hexc}")
        c, m, y, k = rgb_to_cmyk(rgb)
        return {
            "requested": hexc,
            "status": "needs_licensed_pantone_data",
            "exact_pantone": None,
            "requires": "PANTONE+ / 官方专色库授权数据（§44 红线：不得把估算当作精确色彩标准）",
            "process_equivalent_cmyk": {"c": c, "m": m, "y": y, "k": k},
            "process_equivalent_note": "以上为 generic 公式四色(CMYK)等价值，仅供近似参考，非 Pantone 专色编号，不能替代授权库匹配",
            "fabricated": False,
        }


if __name__ == "__main__":
    eng = ColorEngine()
    assert contrast_ratio("#000000", "#FFFFFF") == 21.0, "黑白对比应为 21:1"
    c = eng.contrast("#777777", "#FFFFFF")
    assert 4 < c["ratio"] < 5, c
    cv = eng.convert("#FF0000")
    assert cv["rgb"] == {"r": 255, "g": 0, "b": 0}
    assert cv["cmyk"]["c"] == 0.0 and abs(cv["cmyk"]["m"] - 100) < 1e-6 and cv["cmyk"]["k"] == 0.0
    de = eng.delta_e("#808080", "#7f7f7f")["delta_e"]
    assert de < 1.5, de
    # 真实感知色差：纯红 vs 纯蓝 ΔE 很大
    assert eng.delta_e("#FF0000", "#0000FF")["delta_e"] > 50
    harm = eng.harmony("#3366CC", "triadic")
    assert len(harm["colors"]) == 3
    pan = eng.pantone_match("#1F4E79")
    assert pan["status"] == "needs_licensed_pantone_data" and pan["exact_pantone"] is None and pan["fabricated"] is False
    # --- palette_from_image 自测：真实驱动被测对象，断言调色板命中真像素色（非恒真） ---
    palette_note = ""
    if PIL_AVAILABLE:
        import tempfile
        from PIL import Image as _Img
        _green = (30, 140, 60)
        _red = (240, 30, 30)
        with tempfile.TemporaryDirectory() as _td:
            # 1) 双色各半无损 PNG：应分离出两个主色，逐色以 ciede2000 命中真实源色，占比近 50/50
            _im2 = _Img.new("RGB", (100, 100))
            for _y in range(100):
                for _x in range(100):
                    _im2.putpixel((_x, _y), _green if _y < 50 else _red)
            _p2 = _td + "/two.png"
            _im2.save(_p2)
            _pp2 = eng.palette_from_image(_p2, k=4)["palette"]
            assert len(_pp2) >= 2, ("双色图应至少分离出 2 个主色", _pp2)
            for _want in (_green, _red):
                _whex = rgb_to_hex(_want).upper()
                _best = min(_pp2, key=lambda e: eng.delta_e(_whex, e["hex"])["delta_e"])
                assert eng.delta_e(_whex, _best["hex"])["delta_e"] < 3.0, ("主色未命中真实像素色", _whex, _best)
            _halves = sorted((e["percent"] for e in _pp2[:2]), reverse=True)
            assert abs(_halves[0] - 50.0) < 8.0 and abs(_halves[1] - 50.0) < 8.0, ("占比应近 50/50", _halves)
            assert abs(sum(e["percent"] for e in _pp2) - 100.0) < 1.5, _pp2
            # 2) 纯单色：Top-1 必即该源色且几乎占满（读真像素，非编造）
            _im1 = _Img.new("RGB", (64, 64), _green)
            _p1 = _td + "/solid.png"
            _im1.save(_p1)
            _top = eng.palette_from_image(_p1, k=3)["palette"][0]
            assert eng.delta_e(rgb_to_hex(_green).upper(), _top["hex"])["delta_e"] < 3.0, ("纯色 Top-1 偏离源色", _top)
            assert _top["percent"] > 90.0, ("纯色应几乎占满", _top)
        palette_note = "  palette=真像素命中(双色50/50+纯色)"
    else:
        palette_note = "  palette=SKIPPED(Pillow 不可用，未伪造覆盖)"
    try:
        eng.contrast("#zzzzzz", "#FFFFFF")
        raise AssertionError("非法色应抛 KeyError")
    except KeyError:
        pass
    print("[PASS] color engine self-test  contrast(white/black)=21  triadic=%d  deltaE(red,blue)=%.1f%s"
          % (len(harm["colors"]), eng.delta_e("#FF0000", "#0000FF")["delta_e"], palette_note))
