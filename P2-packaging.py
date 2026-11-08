"""
造境 AI 设计智能体 · 包装与变量数据引擎（Packaging / Variable Data Engine）
========================================================================
任务书 §46 包装 Engine + §47 变量包装。

本引擎只把**真实可算**的部分做成正式能力，其余按 §2 红线诚实标注 needs_external，绝不伪造：

真实实现：
- Dieline 刀版几何：由成品尺寸 + 出血 + 安全边距，推导裁切框 / 出血框 / 安全框三者尺寸与面积、
  最小 artwork 尺寸，并校验非法输入（负出血、安全区大于半个面等）。纯几何算术。
- Imposition 拼版（up-to-N）：给定小样与纸张，真实计算每行/每列可放数（含旋转取优）、单张满版数、
  所需纸张数与纸张利用率。纯算术。
- Variable Data Merge 变量数据合并（§47）：真实读取 CSV / XLSX(openpyxl) / JSON，按字段映射逐行生成
  变体记录（Product Name / SKU / Flavor / Weight / Color / Price 等）。列缺失如实记 missing_columns 且该
  字段留空，绝不臆造数据填充。
- Barcode 条码渲染（§46，EAN-13 / EAN-8）：纯标准算法——真实校验位计算 + L/G/R 编码表 + 首字奇偶
  模式（parity pattern）+ 起止/中间 guard 条纹，逐模块生成 0/1 位串后用 Pillow 渲染成真实 PNG（含静区
  quiet zone、可选人眼可读数字）。不依赖任何外部条码库。
- Panel Relation / 刀版面板拓扑（§46，analyze_panel_topology）：给定**结构化矢量刀版**（一组轴对齐面板
  矩形）真实计算邻接关系、折线（共享边）、折叠连通性、邻接度、外轮廓裁切线长——纯几何（矩形求交 + BFS
  连通分量），不用 CV、不猜语义。§46 的 "Panel Relation" 由此真交付。

诚实标注为未实现（不伪造成功）：
- 栅格图像 → 面板 的 Panel Recognition：Phase 47 起由**独立第 25 引擎 panel_recognition_engine** 真实交付
  （Otsu 二值化 + 轴对齐游程线抽取 + 支撑屏障连通域，零 CV/ML 模型）。本引擎不承担图像解码——
  矢量刀版的面板关系/拓扑由 analyze_panel_topology 真实交付，二者输入形态与职责严格区分。
- QRCode 二维码（§46，Phase 57 起**真实实现**，ISO/IEC 18004）：v1..v10 × L/M/Q/H，Numeric /
  Alphanumeric / Byte(UTF-8，含中文) 三种模式，纯标准库自写 GF(256) Reed-Solomon、BCH 格式/版本
  信息、之字形模块放置与 8 掩码罚分选择，出真 PNG。Kanji/ECI 模式与 v>10 载荷不投（如实抛错）。
- Code-128 / DataMatrix / PDF417 等其余码制：仍**未实现**，请求则如实返回 needs_external，绝不伪造
  图像。Code-128 留守的两条理由（2026-10-11 按实测改核）：(1) 其 107 项模式表不可由几何推导；
  (2) 本机 cv2.barcode 只出 BarcodeDetector、**不出编码器**，一维侧只能读不能画，而本轮以第三方
  夹具（reportlab 自家 Code-128 流水线 + 独立复算校验字符号一致）在 3 枚 × 12 形制 × 4 边距 = 144
  次试读中**未证得** Code-128（同一套光栅画出的 EAN-13 可逐字读回，作通道正对照）。「未证得」不等于
  「不存在」；抄错一个索引既不能算、也不能被读回证伪，§2 红线不投。
  旧口径「本机 OpenCV 一维解码器需 DNN 权重文件、不可用」系 P57 误记，已被上述实测反驳——该解码器
  对本机真渲染的 EAN-13 / EAN-8 / UPC_A 均可逐字读回（凭证见 L2/L4 Phase 62 块注）。
"""

from __future__ import annotations

import csv
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("packaging_engine")

try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except Exception:  # pragma: no cover
    openpyxl = None  # type: ignore
    OPENPYXL_AVAILABLE = False

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = ImageDraw = None  # type: ignore
    PIL_AVAILABLE = False


# ===========================================================================
# QR（ISO/IEC 18004）数学内核 —— Phase 57，纯标准库，零第三方条码/二维码库依赖
# ===========================================================================
# 为什么必须自己写：PyInstaller 静态扫描会把任何 import 的条码库收进 EXE，而 §2 红线
# 要求"真实可算才交付"。QR 的全部成分（GF(256) 上的 Reed-Solomon、BCH 校验的格式/版本
# 信息、模块放置的之字形扫描、8 个掩码的罚分选择）都是**确定性算术**，标准库足够。
_QR_EXP = [0] * 512
_QR_LOG = [0] * 256


def _qr_init_gf() -> None:
    """GF(256) 指数/对数表，本原多项式 0x11D，生成元 α=2。"""
    x = 1
    for i in range(255):
        _QR_EXP[i] = x
        _QR_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _QR_EXP[i] = _QR_EXP[i - 255]


_qr_init_gf()


def _qr_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _QR_EXP[_QR_LOG[a] + _QR_LOG[b]]


def _qr_rs_gen(deg: int) -> List[int]:
    """g(x) = Π_{i<deg}(x − α^i)，返回**降幂**系数表（len = deg+1，首项 1）。"""
    g = [1]
    for i in range(deg):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c                                 # ·x
            nxt[j + 1] ^= _qr_mul(c, _QR_EXP[i])        # ·α^i
        g = nxt
    return g


def _qr_rs_ec(data: List[int], deg: int, gen: List[int]) -> List[int]:
    """data·x^deg mod g(x)，返回 deg 个纠错码字（降幂，与 data 同序拼接即为合法码字）。"""
    rem = [0] * deg
    for sym in data:
        factor = sym ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(deg):
            rem[i] ^= _qr_mul(factor, gen[i + 1])
    return rem


def _qr_bch(data: int, gen: int, check_bits: int, xor_mask: int) -> int:
    """系统式 BCH：((data<<check_bits) | data<<check_bits mod gen) ^ xor_mask。

    格式信息用 gen=0x537 / check_bits=10 / mask=0x5412；版本信息用 gen=0x1F25 / 12 / 0。
    """
    rem = data << check_bits
    gd = gen.bit_length() - 1
    for i in range(rem.bit_length() - 1, gd - 1, -1):
        if (rem >> i) & 1:
            rem ^= gen << (i - gd)
    return ((data << check_bits) | (rem & ((1 << check_bits) - 1))) ^ xor_mask


def _qr_fmt_pos_a(bit: int) -> Any:
    """格式信息 A 副本（绕左上定位图形）：bit0..5 列 8 行 0..5，跳过定时行 6。"""
    if bit < 6:
        return (bit, 8)
    if bit == 6:
        return (7, 8)
    if bit == 7:
        return (8, 8)
    if bit == 8:
        return (8, 7)
    return (8, 14 - bit)


def _qr_fmt_pos_b(bit: int, side: int) -> Any:
    """格式信息 B 副本（ISO/IEC 18004 图 24，与 A 副本同向、非转置）：
    bit0..7 沿**行 8** 自右下角向左（列 = side-1-bit），bit8..14 沿**列 8** 自下向上
    （行 = side-15+bit）。此处曾写成 `(side-1-bit, 8) if bit<7 else (8, side-15+bit)`，
    等价于把位序整体做了 i <-> 14-i 的**比特反转**摆放（两段互换所属边），
    导致 v1..v10 中掩码/等级不同的行能否被第三方检测器读回全凭巧合。
    """
    return (8, side - 1 - bit) if bit < 8 else (side - 15 + bit, 8)


def _qr_ver_pos(bit: int, side: int, copy: int) -> Any:
    a, b = side - 11 + bit % 3, bit // 3
    return (b, a) if copy == 0 else (a, b)


class PackagingEngine:
    def __init__(self, store_dir: Optional[Path] = None):
        self.store_dir = Path(store_dir) if store_dir else None
        if self.store_dir:
            self.store_dir.mkdir(parents=True, exist_ok=True)

    # ---- Dieline 刀版/出血/安全区 ----
    def dieline(self, width: float, height: float, bleed: float = 0.0,
                safe: float = 0.0, units: str = "mm") -> Dict[str, Any]:
        width, height, bleed, safe = float(width), float(height), float(bleed), float(safe)
        warnings: List[str] = []
        if width <= 0 or height <= 0:
            raise ValueError("成品宽高必须为正数")
        if bleed < 0:
            warnings.append("出血为负，已按 0 处理")
            bleed = 0.0
        if safe < 0:
            warnings.append("安全边距为负，已按 0 处理")
            safe = 0.0
        if safe >= min(width, height) / 2:
            warnings.append("安全边距 ≥ 半个面，安全框将退化/翻转，请核对")
        trim = [0.0, 0.0, width, height]
        bleed_box = [-bleed, -bleed, width + bleed, height + bleed]
        safe_box = [safe, safe, width - safe, height - safe]
        # Phase 51 数据桥：刀版响应自带**结构化矢量面板**，可直接喂给
        # analyze_panel_topology / POST /packaging/panel-topology，两端不再是孤岛。
        # 诚实边界：dieline 只知道成品面尺寸，不知道折线/展开结构，故只输出**一个**
        # 成品面面板并显式标注 panel_basis；绝不按"猜测的盒型"臆造多面板展开图。
        panels = [{"id": "trim_face", "x": trim[0], "y": trim[1],
                   "width": width, "height": height,
                   "role_basis": "finished-face-only"}]
        return {
            "units": units,
            "finished": {"width": width, "height": height},
            "trim_box": trim,
            "bleed_box": bleed_box,
            "safety_box": safe_box if safe_box[2] > safe_box[0] and safe_box[3] > safe_box[1] else None,
            "min_artwork_size": {"width": width + 2 * bleed, "height": height + 2 * bleed},
            "areas": {
                "trim_area": round(width * height, 3),
                "bleed_area": round((width + 2 * bleed) * (height + 2 * bleed), 3),
                "safe_area": round(max(0.0, width - 2 * safe) * max(0.0, height - 2 * safe), 3),
            },
            "warnings": warnings,
            "panels": panels,
            "panel_count": len(panels),
            "panel_basis": "trim_face_only",
            "panel_note": ("仅成品面单面板：刀版入参不含折线/展开信息，故不臆造多面板盒型；"
                           "panels 可直接作为 panel-topology 的入参形成真实血缘边"),
        }

    # ---- Imposition 拼版 up-to-N ----
    def imposition(self, die_width: float, die_height: float, count: int,
                   sheet_width: float, sheet_height: float, gap: float = 0.0) -> Dict[str, Any]:
        die_width, die_height = float(die_width), float(die_height)
        sheet_width, sheet_height = float(sheet_width), float(sheet_height)
        count = int(count)
        gap = float(gap)
        if min(die_width, die_height, sheet_width, sheet_height) <= 0 or count <= 0:
            raise ValueError("小样/纸张尺寸与数量必须为正")

        def fit(dw: float, dh: float) -> Dict[str, Any]:
            cols = math.floor((sheet_width + gap) / (dw + gap)) if (dw + gap) > 0 else 0
            rows = math.floor((sheet_height + gap) / (dh + gap)) if (dh + gap) > 0 else 0
            return {"orientation": "landscape" if dw >= dh else "portrait",
                    "die": [dw, dh], "per_row": max(0, cols), "per_col": max(0, rows),
                    "n_up": max(0, cols * rows)}

        straight = fit(die_width, die_height)
        rotated = fit(die_height, die_width)
        best = straight if straight["n_up"] >= rotated["n_up"] else rotated
        if best["n_up"] == 0:
            return {"feasible": False, "reason": "单个小样在给定纸张/间距下放不下",
                    "straight": straight, "rotated": rotated}
        n_up = best["n_up"]
        sheets = math.ceil(count / n_up)
        die_area = best["die"][0] * best["die"][1]
        sheet_area = sheet_width * sheet_height
        utilization = (count * die_area) / (sheets * sheet_area) * 100
        return {
            "feasible": True, "chosen": best,
            "alternatives": {"straight": straight, "rotated": rotated},
            "n_up": n_up, "sheets_needed": sheets,
            "total_slots": sheets * n_up, "wasted_slots": sheets * n_up - count,
            "sheet_utilization_percent": round(utilization, 2),
        }

    # ---- Variable Data Merge（§47）----
    def _read_tabular(self, source: Path) -> List[Dict[str, Any]]:
        ext = source.suffix.lower()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"数据源文件不存在: {source}")
        if ext == ".csv":
            with source.open("r", encoding="utf-8-sig", newline="") as f:
                return [dict(r) for r in csv.DictReader(f)]
        if ext == ".json":
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("JSON 数据源需为对象数组(list of records)")
            return [r for r in data if isinstance(r, dict)]
        if ext in (".xlsx", ".xlsm"):
            if not OPENPYXL_AVAILABLE:
                raise RuntimeError("未安装 openpyxl，无法读取 XLSX（不伪造行数据）")
            wb = openpyxl.load_workbook(source, read_only=True, data_only=True)  # type: ignore
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if not rows:
                return []
            header = [str(c) if c is not None else "" for c in rows[0]]
            return [dict(zip(header, r)) for r in rows[1:]]
        raise ValueError(f"不支持的数据源格式: {ext}（可用 .csv/.json/.xlsx）")

    def variable_data_merge(self, source: Path, field_map: Optional[Dict[str, str]] = None,
                            limit: Optional[int] = None) -> Dict[str, Any]:
        source = Path(source)
        rows = self._read_tabular(source)
        available_cols = sorted({str(k) for r in rows for k in r.keys()})
        missing: List[str] = []
        records: List[Dict[str, Any]] = []
        body = rows[:limit] if limit else rows
        for ri, r in enumerate(body):
            if field_map:
                rec: Dict[str, Any] = {}
                for out_field, col in field_map.items():
                    if col in r and r[col] not in (None, ""):
                        rec[out_field] = r[col]
                    else:
                        rec[out_field] = None
                        key = f"{col}"
                        if key not in missing:
                            missing.append(key)
            else:
                rec = {str(k): v for k, v in r.items()}
            records.append({"row_index": ri, "variant": rec})
        return {
            "source": str(source), "format": source.suffix.lower().lstrip("."),
            "total_rows": len(rows), "merged_rows": len(records),
            "available_columns": available_cols,
            "requested_fields": sorted(field_map.keys()) if field_map else available_cols,
            "missing_columns": missing,
            "fabricated": False, "records": records,
        }

    # ---- 条码渲染（§46，EAN-13 / EAN-8，真实标准算法）----
    # L-code（奇parity，左半区可选）、G-code（偶parity，= R 的反向）、R-code（偶parity，右半区）
    _EAN_L = {
        "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
        "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011",
    }
    _EAN_G = {
        "0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
        "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111",
    }
    _EAN_R = {
        "0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010", "4": "1011100",
        "5": "1001110", "6": "1010000", "7": "1000100", "8": "1001000", "9": "1110100",
    }
    # EAN-13 首位数字决定左半区 6 位各自的 parity 组合（L=奇, G=偶）
    _EAN13_PARITY = {
        "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
        "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL",
    }

    @classmethod
    def _ean13_check_digit(cls, digits12: str) -> str:
        # 权重 1,3 交替（首位权重 1），校验位 = (10 - 加权和对10取余) % 10
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits12))
        return str((10 - total % 10) % 10)

    @classmethod
    def _ean8_check_digit(cls, digits7: str) -> str:
        # EAN-8 权重 3,1,3,1,3,1,3（首位权重 3）
        total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits7))
        return str((10 - total % 10) % 10)

    @classmethod
    def _ean13_bits(cls, code13: str) -> str:
        left, right = code13[1:7], code13[7:13]
        parity = cls._EAN13_PARITY[code13[0]]
        bits = "101"
        for ch, p in zip(left, parity):
            bits += (cls._EAN_L if p == "L" else cls._EAN_G)[ch]
        bits += "01010"
        for ch in right:
            bits += cls._EAN_R[ch]
        bits += "101"
        return bits

    @classmethod
    def _ean8_bits(cls, code8: str) -> str:
        bits = "101"
        for ch in code8[:4]:
            bits += cls._EAN_L[ch]
        bits += "01010"
        for ch in code8[4:]:
            bits += cls._EAN_R[ch]
        bits += "101"
        return bits

    def render_barcode(self, code: str, symbology: str = "EAN13", scale: int = 2,
                       height: int = 60, quiet: Optional[int] = None, draw_text: bool = True,
                       ec_level: str = "M", output_path: Optional[str] = None) -> Dict[str, Any]:
        """条码/二维码渲染统一入口。EAN-13 / EAN-8 / QR 真实实现；其余码制如实 needs_external。

        quiet 缺省按码制各取其标准静区（一维 11 模块、QR 4 模块），故历史一维调用方逐字节不受影响。
        """
        sym = (symbology or "").upper()
        if sym in ("QR", "QRCODE"):
            return self.render_qr(code, ec_level=ec_level, scale=scale, output_path=output_path,
                                  quiet=4 if quiet is None else quiet)
        if sym not in ("EAN13", "EAN8"):
            return {
                "status": "needs_external", "implemented": False,
                "reason": f"码制 {symbology} 暂未真实实现（§46 本引擎支持 EAN-13 / EAN-8 / QR(v1..v10)；"
                          "Code-128 的 107 项模式表不可几何推导；本机 cv2.barcode 只出解码器不出编码器，"
                          "本轮以第三方夹具 144 次试读未证得该码制（EAN-13 通道正对照成立），"
                          "抄错一个索引既不能算也不能被读回证伪；"
                          "DataMatrix/PDF417 同理）。按 §2 红线不伪造条码图像。",
                "requested": {"code": code, "symbology": symbology},
            }
        quiet = 11 if quiet is None else quiet
        digits = "".join(ch for ch in str(code) if ch.isdigit())
        if sym == "EAN13":
            if len(digits) == 12:
                full = digits + self._ean13_check_digit(digits)
                check_digit = full[-1]
            elif len(digits) == 13:
                expected = self._ean13_check_digit(digits[:12])
                if digits[12] != expected:
                    raise ValueError(f"EAN-13 校验位非法：给定 {digits[12]}，应为 {expected}")
                full, check_digit = digits, digits[12]
            else:
                raise ValueError("EAN-13 需 12 位(自动补校验位)或 13 位数字")
            bits = self._ean13_bits(full)
        else:  # EAN8
            if len(digits) == 7:
                full = digits + self._ean8_check_digit(digits)
                check_digit = full[-1]
            elif len(digits) == 8:
                expected = self._ean8_check_digit(digits[:7])
                if digits[7] != expected:
                    raise ValueError(f"EAN-8 校验位非法：给定 {digits[7]}，应为 {expected}")
                full, check_digit = digits, digits[7]
            else:
                raise ValueError("EAN-8 需 7 位(自动补校验位)或 8 位数字")
            bits = self._ean8_bits(full)

        scale = max(1, int(scale))
        height = max(10, int(height))
        quiet = max(2, int(quiet))
        bw = len(bits) * scale
        quiet_px = quiet * scale
        margin_y = (10 if not draw_text else 22)
        img_w = bw + 2 * quiet_px
        img_h = height + 2 * margin_y
        if not PIL_AVAILABLE:
            raise RuntimeError("未安装 Pillow，无法渲染条码位图（不伪造图像文件）")
        img = Image.new("1", (img_w, img_h), 1)  # 1-bit，背景白
        draw = ImageDraw.Draw(img)
        y0, y1 = margin_y, margin_y + height
        x = quiet_px
        for mod in bits:
            if mod == "1":
                draw.rectangle([x, y0, x + scale - 1, y1], fill=0)
            x += scale
        out = Path(output_path) if output_path else (
            (self.store_dir / f"barcode_{sym}_{full}.png") if self.store_dir
            else Path(f"barcode_{sym}_{full}.png"))
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, format="PNG")
        return {
            "status": "ok", "implemented": True, "symbology": sym,
            "code": full, "check_digit": check_digit,
            "module_count": len(bits), "bit_pattern": bits,
            "quiet_zone_modules": quiet,
            "width_px": img_w, "height_px": img_h, "scale": scale,
            "draw_text": draw_text, "format": "png",
            "output_path": str(out), "bytes": out.stat().st_size,
            "fabricated": False,
        }

    # ---- QR 二维码（Phase 57，ISO/IEC 18004，纯标准库真实实现）----
    QR_MAX_VERSION = 10
    _QR_EC_LEVELS = ("L", "M", "Q", "H")
    _QR_EC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
    _QR_TOTAL_CODEWORDS = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
                           6: 172, 7: 196, 8: 242, 9: 292, 10: 346}
    _QR_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
    _QR_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
    # (版本, 纠错等级) -> (每块纠错码字数, [(块数, 每块数据码字数), ...])（数据少的块排在前面）
    # 这是 QR 里**唯一不能由几何推导**的规格表，故两道把关（P57 设计口径）：
    #   ① 导入期算术自检：Σ(块数×(数据+纠错)) 必须逐版等于 _QR_TOTAL_CODEWORDS（几何反算值，
    #      见 p57_qr_capacity_probe.txt：v1..v10 全部与已发表总码字数/余比特零偏差）；
    #   ② L2/L4 第三方 OpenCV 逐 (版本×等级) 读回载荷，逐字相等才算这一行的证据。
    #   任何一行抄错都会在 ① 立刻 RuntimeError（和式对不上），或在 ② 当场读不回。
    #
    # 本表 40 行的**出处**（P57 实测，逐行两把尺子，不采信任何记忆与转抄）：
    #   尺子甲＝独立 RS 校验子发现：取 cv2.QRCodeEncoder 在指定 (version, level) 生成的参考图，
    #     用与产品无关的 ISO 几何反读码字流，对每个算术可行的 (n, ec) 候选按两级轮询拆块求校验子，
    #     40 行**每行恰有一个**候选使 n 个块同时全零（误中概率 ~2^(-8·n·ec)，见
    #     p57_qr_table_probe.json 的 phase_a_hits：nhit 恒为 1）；
    #   尺子乙＝已发表 Byte 字符容量表（ISO/IEC 18004 Table 8，逐版 17/14/11/7、32/26/20/14、
    #     53/42/32/24、78/62/46/34、106/84/60/44 … 271/213/151/119）：容量 = d-2（v≤9）或 d-3（v10），
    #     与甲的 d=Σ数据码字数 逐行吻合 ⇒ 和式与「真是 ISO 那一行」两侧同时钉住。
    #   订正记录（原表 40 行中 11 行与上述两把尺子相左，抄错后果不是报错而是产出读不出的码）：
    #     和式即不过、导入自检当场炸的 5 行：(9,M) 6块×22→5块×22、(9,Q) 8块×22→8块×20、
    #       (9,H) 6块×22→8块×24、(10,Q) 5块×24→8块×24、(10,H) 8块×28 的 (6,15),(2,14)→(6,15),(2,16)；
    #     **和式对得上但不是 ISO 那一行**的 6 行（① 单独拦不住，正是必须有 ② 的理由）：
    #       (4,Q) 4块×13→2块×26、(6,Q) 4块×22→4块×24、(6,H) 5块×22→4块×28、
    #       (7,Q) 6块×17→6块×18、(7,H) 6块×20→5块×26、(8,Q) 6块×20→6块×22。
    _QR_BLOCKS: Dict[Any, Any] = {
        (1, "L"): (7, [(1, 19)]), (1, "M"): (10, [(1, 16)]),
        (1, "Q"): (13, [(1, 13)]), (1, "H"): (17, [(1, 9)]),
        (2, "L"): (10, [(1, 34)]), (2, "M"): (16, [(1, 28)]),
        (2, "Q"): (22, [(1, 22)]), (2, "H"): (28, [(1, 16)]),
        (3, "L"): (15, [(1, 55)]), (3, "M"): (26, [(1, 44)]),
        (3, "Q"): (18, [(2, 17)]), (3, "H"): (22, [(2, 13)]),
        (4, "L"): (20, [(1, 80)]), (4, "M"): (18, [(2, 32)]),
        (4, "Q"): (26, [(2, 24)]), (4, "H"): (16, [(4, 9)]),
        (5, "L"): (26, [(1, 108)]), (5, "M"): (24, [(2, 43)]),
        (5, "Q"): (18, [(2, 15), (2, 16)]), (5, "H"): (22, [(2, 11), (2, 12)]),
        (6, "L"): (18, [(2, 68)]), (6, "M"): (16, [(4, 27)]),
        (6, "Q"): (24, [(4, 19)]), (6, "H"): (28, [(4, 15)]),
        (7, "L"): (20, [(2, 78)]), (7, "M"): (18, [(4, 31)]),
        (7, "Q"): (18, [(2, 14), (4, 15)]), (7, "H"): (26, [(4, 13), (1, 14)]),
        (8, "L"): (24, [(2, 97)]), (8, "M"): (22, [(2, 38), (2, 39)]),
        (8, "Q"): (22, [(4, 18), (2, 19)]), (8, "H"): (26, [(4, 14), (2, 15)]),
        (9, "L"): (30, [(2, 116)]), (9, "M"): (22, [(3, 36), (2, 37)]),
        (9, "Q"): (20, [(4, 16), (4, 17)]), (9, "H"): (24, [(4, 12), (4, 13)]),
        (10, "L"): (18, [(2, 68), (2, 69)]), (10, "M"): (26, [(4, 43), (1, 44)]),
        (10, "Q"): (24, [(6, 19), (2, 20)]), (10, "H"): (28, [(6, 15), (2, 16)]),
    }

    @classmethod
    def _qr_structure(cls, ver: int, level: str):
        ec, groups = cls._QR_BLOCKS[(ver, level)]
        data_lens: List[int] = []
        for count, d in groups:
            data_lens += [d] * count
        return data_lens, ec

    @classmethod
    def _qr_data_total(cls, ver: int, level: str) -> int:
        lens, _ec = cls._qr_structure(ver, level)
        return sum(lens)

    @staticmethod
    def _qr_groups(data_lens: List[int], ec_per: int) -> List[Any]:
        """把逐块数据长度序列压回 [(块数, 每块数据码字数), ...]（连续同长合并）。

        与 `_qr_structure` 互为逆运算，供对外申报 `blocks` 字段用；不做查表，故表改一处不两漏。
        """
        out: List[List[int]] = []
        for d in data_lens:
            if out and out[-1][1] == d:
                out[-1][0] += 1
            else:
                out.append([1, d])
        return [(c, d) for c, d in out]

    @classmethod
    def _qr_alignment_centers(cls, ver: int, side: int) -> List[int]:
        """校正图形中心由几何**推导**（非查表）：n = 2 + v//7，末位 = side-7，步距取偶。"""
        if ver < 2:
            return []
        n = 2 + ver // 7
        last = side - 7
        step = (last - 6) // (n - 1)
        step += step % 2
        return [6 + step * i for i in range(n - 1)] + [last]

    @classmethod
    def _qr_mode(cls, text: str) -> str:
        if text and all("0" <= c <= "9" for c in text):
            return "Numeric"
        if text and all(c in cls._QR_ALNUM for c in text):
            return "Alphanumeric"
        return "Byte"

    @classmethod
    def _qr_cc_bits(cls, mode: str, ver: int) -> int:
        tbl = {"Numeric": (10, 12, 15), "Alphanumeric": (9, 11, 13), "Byte": (8, 16, 16)}
        t = tbl[mode]
        return t[0] if ver <= 9 else (t[1] if ver <= 26 else t[2])

    @staticmethod
    def _qr_push(val: int, n: int) -> List[int]:
        return [(val >> (n - 1 - i)) & 1 for i in range(n)]

    @classmethod
    def _qr_payload_bits(cls, mode: str, text: str) -> List[int]:
        bits: List[int] = []
        if mode == "Numeric":
            i = 0
            while i + 3 <= len(text):
                bits += cls._qr_push(int(text[i:i + 3]), 10)
                i += 3
            rem = len(text) - i
            if rem == 2:
                bits += cls._qr_push(int(text[i:]), 7)
            elif rem == 1:
                bits += cls._qr_push(int(text[i:]), 4)
        elif mode == "Alphanumeric":
            vals = [cls._QR_ALNUM.index(c) for c in text]
            for i in range(0, len(vals) - 1, 2):
                bits += cls._qr_push(vals[i] * 45 + vals[i + 1], 11)
            if len(vals) % 2:
                bits += cls._qr_push(vals[-1], 6)
        else:
            for b in text.encode("utf-8"):
                bits += cls._qr_push(b, 8)
        return bits

    @classmethod
    def _qr_pick_version(cls, mode: str, nchars: int, payload_bits: int, level: str) -> int:
        for ver in range(1, cls.QR_MAX_VERSION + 1):
            need = 4 + cls._qr_cc_bits(mode, ver) + payload_bits
            if need <= cls._qr_data_total(ver, level) * 8:
                return ver
        raise ValueError(
            f"载荷超出本引擎已实现的 QR 版本范围（v1..v{cls.QR_MAX_VERSION}，"
            f"纠错等级 {level}）：{nchars} 字符 / mode={mode}。按 §2 红线不伪造图像。")

    @classmethod
    def _qr_build_stream(cls, mode: str, text: str, nchars: int, ver: int, level: str):
        """返回 (交织后的全部码字表, 各块数据长度, 每块纠错数, 数据码字数, 纠错码字数)。

        位流组装严格按 ISO/IEC 18004 §8.4：模式指示 4 位 → 字符数指示 → 载荷 →
        终止符(≤4 个 0，容量不足则截短) → 补 0 到字节边界 → 交替 0xEC/0x11 填充码字。
        """
        payload = cls._qr_payload_bits(mode, text)
        bits = cls._qr_push(int({"Numeric": 1, "Alphanumeric": 2, "Byte": 4}[mode]), 4)
        bits += cls._qr_push(nchars, cls._qr_cc_bits(mode, ver))
        bits += payload
        capacity = cls._qr_data_total(ver, level) * 8
        assert len(bits) <= capacity, ("版本挑选失效：位流超出容量", len(bits), capacity)
        bits += [0] * min(4, capacity - len(bits))              # 终止符
        if len(bits) % 8:                                       # 补齐到字节边界
            bits += [0] * (8 - len(bits) % 8)
        data_cw = [int("".join(map(str, bits[i:i + 8])), 2)
                   for i in range(0, len(bits), 8)]
        pad = (0xEC, 0x11)                                      # 填充码字交替，首个 0xEC
        k = 0
        while len(data_cw) < capacity // 8:
            data_cw.append(pad[k % 2])
            k += 1
        data_lens, ec_per = cls._qr_structure(ver, level)
        blocks_d, idx = [], 0
        for dl in data_lens:
            blocks_d.append(data_cw[idx:idx + dl])
            idx += dl
        assert idx == len(data_cw) == capacity // 8, (idx, len(data_cw))
        gen = _qr_rs_gen(ec_per)
        blocks_e = [_qr_rs_ec(b, ec_per, gen) for b in blocks_d]
        out: List[int] = []
        for j in range(max(len(b) for b in blocks_d)):      # 数据按块轮询交织
            for b in blocks_d:
                if j < len(b):
                    out.append(b[j])
        for j in range(ec_per):                             # 纠错同样轮询交织
            for b in blocks_e:
                out.append(b[j])
        n_blocks, data_cw_n = len(data_lens), sum(data_lens)
        # 真不变式：交织输出 = 数据码字 + 块数×每块纠错数 = 几何反算的总码字数。
        # （P57 首轮此处误写成 `== capacity // 8`，即拿「数据码字数」当「全码字数」，
        # 于是凡 ec>0 的行一律当场炸——那条断言本身是错的，见 p57_b4_diag.txt。）
        total_cw = cls._QR_TOTAL_CODEWORDS[ver]
        assert len(out) == data_cw_n + ec_per * n_blocks == total_cw, (
            "交织后码字数与几何总码字数不符（该行分块表与版本几何矛盾）：",
            f"ver={ver} level={level} 块数={n_blocks} 每块纠错={ec_per} "
            f"数据码字={data_cw_n} 交织输出={len(out)} 几何总码字={total_cw}")
        return out, data_lens, ec_per, data_cw_n, ec_per * n_blocks

    @classmethod
    def _qr_mask_bit(cls, mask: int, r: int, c: int) -> bool:
        if mask == 0:
            return (r + c) % 2 == 0
        if mask == 1:
            return r % 2 == 0
        if mask == 2:
            return c % 3 == 0
        if mask == 3:
            return (r + c) % 3 == 0
        if mask == 4:
            return (r // 2 + c // 3) % 2 == 0
        if mask == 5:
            return (r * c) % 2 + (r * c) % 3 == 0
        if mask == 6:
            return ((r * c) % 2 + (r * c) % 3) % 2 == 0
        return ((r + c) % 2 + (r * c) % 3) % 2 == 0

    @staticmethod
    def _qr_penalty(mat: List[List[int]]) -> int:
        """ISO 四条罚分：①同色≥5 连续 ②2x2 同色 ③1:1:3:1:1 ④黑模块比例（每 5% 一档）。"""
        side = len(mat)
        rows = mat
        cols = [[mat[r][c] for r in range(side)] for c in range(side)]
        score = 0
        for line in rows + cols:                            # ①
            run = 1
            for i in range(1, side):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
        for r in range(side - 1):                           # ②
            for c in range(side - 1):
                v = mat[r][c]
                if v == mat[r][c + 1] == mat[r + 1][c] == mat[r + 1][c + 1]:
                    score += 3
        p1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]             # ③
        p2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
        for line in rows + cols:
            for i in range(side - 10):
                w = line[i:i + 11]
                if w == p1 or w == p2:
                    score += 40
        total = side * side                                 # ④
        dark = sum(sum(r) for r in mat)
        score += 10 * (abs(dark * 100 - total * 50) // (total * 5))
        return score

    @classmethod
    def _qr_function_layer(cls, ver: int, side: int):
        """功能图形层：返回值矩阵、保留位标记、以及数据模块的之字形写入顺序。"""
        base = [[0] * side for _ in range(side)]
        res = [[False] * side for _ in range(side)]

        def put(r: int, c: int, v: int) -> None:
            base[r][c] = v
            res[r][c] = True

        for (r0, c0) in ((0, 0), (0, side - 7), (side - 7, 0)):      # 定位图形 + 分隔符
            for i in range(-1, 8):
                for j in range(-1, 8):
                    r, c = r0 + i, c0 + j
                    if not (0 <= r < side and 0 <= c < side):
                        continue
                    inside = 0 <= i <= 6 and 0 <= j <= 6
                    dark = inside and (i in (0, 6) or j in (0, 6) or (2 <= i <= 4 and 2 <= j <= 4))
                    put(r, c, 1 if dark else 0)
        for i in range(8, side - 8):                                 # 定时图形
            put(6, i, 1 if i % 2 == 0 else 0)
            put(i, 6, 1 if i % 2 == 0 else 0)
        put(side - 8, 8, 1)                                          # 固定黑模块
        centers = cls._qr_alignment_centers(ver, side)
        last = side - 7
        for cr in centers:                                           # 校正图形
            for cc in centers:
                if (cr, cc) in {(6, 6), (6, last), (last, 6)}:
                    continue
                for i in range(-2, 3):
                    for j in range(-2, 3):
                        put(cr + i, cc + j, 1 if max(abs(i), abs(j)) != 1 else 0)
        for bit in range(15):                                        # 格式信息区先占位
            for pos in (_qr_fmt_pos_a(bit), _qr_fmt_pos_b(bit, side)):
                res[pos[0]][pos[1]] = True
        if ver >= 7:
            for bit in range(18):
                for copy in (0, 1):
                    p = _qr_ver_pos(bit, side, copy)
                    res[p[0]][p[1]] = True
        positions: List[Any] = []                                    # 之字形写入顺序
        col, upward = side - 1, True
        while col > 0:
            if col == 6:
                col -= 1
            for i in range(side):
                r = side - 1 - i if upward else i
                for c in (col, col - 1):
                    if not res[r][c]:
                        positions.append((r, c))
            upward = not upward
            col -= 2
        return base, res, positions

    @classmethod
    def _qr_render_matrix(cls, ver: int, level: str, codewords: List[int], side: int):
        """对 8 个掩码各算一次全图与罚分，返回 (最优图, 掩码, 罚分表, 格式位串, 版本位串)。"""
        base, _res, positions = cls._qr_function_layer(ver, side)
        bits = [b for cw in codewords for b in cls._qr_push(cw, 8)]
        bits += [0] * cls._QR_REMAINDER_BITS[ver]
        assert len(bits) == len(positions), (len(bits), len(positions))
        best = None
        penalties = []
        for mask in range(8):
            mat = [row[:] for row in base]
            for k, (r, c) in enumerate(positions):
                mat[r][c] = bits[k] ^ (1 if cls._qr_mask_bit(mask, r, c) else 0)
            fmt = _qr_bch((cls._QR_EC_BITS[level] << 3) | mask, 0x537, 10, 0x5412)
            for b in range(15):
                v = (fmt >> b) & 1
                ra = _qr_fmt_pos_a(b)
                mat[ra[0]][ra[1]] = v
                rb = _qr_fmt_pos_b(b, side)
                mat[rb[0]][rb[1]] = v
            ver_bits = None
            if ver >= 7:
                ver_bits = _qr_bch(ver, 0x1F25, 12, 0)
                for b in range(18):
                    v = (ver_bits >> b) & 1
                    p0 = _qr_ver_pos(b, side, 0)
                    mat[p0[0]][p0[1]] = v
                    p1 = _qr_ver_pos(b, side, 1)
                    mat[p1[0]][p1[1]] = v
            pen = cls._qr_penalty(mat)
            penalties.append(pen)
            if best is None or pen < best[0]:
                best = (pen, mask, mat, fmt, ver_bits)
        pen, mask, mat, fmt, ver_bits = best
        fmt_bits = format(fmt, "015b")
        v_bits_str = format(ver_bits, "018b") if ver_bits is not None else None
        return mat, mask, penalties, pen, fmt_bits, v_bits_str, positions

    def render_qr(self, text: str, ec_level: str = "M", scale: int = 4, quiet: int = 4,
                  output_path: Optional[str] = None) -> Dict[str, Any]:
        """真实 QR（ISO/IEC 18004）：模式选择 → 版本最小化 → RS 纠错 → 掩码罚分 → Pillow 出真 PNG。

        已实现：v1..v{10} × L/M/Q/H，Numeric/Alphanumeric/Byte(UTF-8，含中文) 三种模式。
        未实现且不伪造：Kanji/ECI 模式；超出 v10 的载荷**直接抛错**而非悄悄截断。
        """
        if text is None or not isinstance(text, str) or not text:
            raise ValueError("QR 载荷不能为空")
        level = (ec_level or "M").upper()
        if level not in self._QR_EC_LEVELS:
            raise ValueError(f"非法纠错等级 {ec_level}，只接受 L/M/Q/H")
        scale = max(1, int(scale))
        quiet = max(0, int(quiet))
        mode = self._qr_mode(text)
        nchars = len(text) if mode != "Byte" else len(text.encode("utf-8"))
        payload_bits = self._qr_payload_bits(mode, text)
        ver = self._qr_pick_version(mode, nchars, len(payload_bits), level)
        side = 21 + 4 * (ver - 1)
        codewords, data_lens, ec_per, data_cw_n, ec_cw_n = self._qr_build_stream(
            mode, text, nchars, ver, level)
        mat, mask, penalties, pen, fmt_bits, v_bits_str, _pos = self._qr_render_matrix(
            ver, level, codewords, side)
        centers = self._qr_alignment_centers(ver, side)
        matrix = ["".join(str(v) for v in row) for row in mat]

        if not PIL_AVAILABLE:
            raise RuntimeError("未安装 Pillow，无法渲染 QR 位图（不伪造图像文件）")
        img_w = img_h = (side + 2 * quiet) * scale
        img = Image.new("1", (img_w, img_h), 1)
        draw = ImageDraw.Draw(img)
        for r in range(side):
            row = matrix[r]
            for c in range(side):
                if row[c] == "1":
                    x0 = (quiet + c) * scale
                    y0 = (quiet + r) * scale
                    draw.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=0)
        import hashlib
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        out = Path(output_path) if output_path else (
            (self.store_dir / f"qr_{level}{ver}_{digest}.png") if self.store_dir
            else Path(f"qr_{level}{ver}_{digest}.png"))
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, format="PNG")
        return {
            "status": "ok", "implemented": True, "fabricated": False, "symbology": "QR",
            "code": text, "mode": mode, "ec_level": level,
            "version": ver, "side": side, "module_count": side * side,
            "implemented_version_range": [1, self.QR_MAX_VERSION],
            "matrix": matrix, "bit_pattern": "".join(matrix),
            "quiet_zone_modules": quiet, "scale": scale,
            "alignment_centers": centers,
            "codewords": codewords, "blocks": [
                {"count": cnt, "data": d, "ec": ec_per} for (cnt, d) in
                self._qr_groups(data_lens, ec_per)],
            "data_codewords": data_cw_n, "ec_codewords": ec_cw_n,
            "total_codewords": data_cw_n + ec_cw_n,
            "remainder_bits": self._QR_REMAINDER_BITS[ver],
            "format_bits": fmt_bits, "version_info_bits": v_bits_str,
            "mask_pattern": mask, "mask_penalties": penalties, "mask_penalty": pen,
            "standard": "ISO/IEC 18004 (mode: Numeric/Alphanumeric/Byte; Kanji/ECI not shipped)",
            "width_px": img_w, "height_px": img_h, "format": "png",
            "output_path": str(out), "bytes": out.stat().st_size,
        }

    # ---- Panel Relation / 刀版面板拓扑（§46，纯几何，非 CV）----
    def analyze_panel_topology(self, panels: List[Dict[str, Any]],
                              tol: float = 1e-6, min_fold: float = 1e-6,
                              units: str = "mm") -> Dict[str, Any]:
        """
        从**结构化矢量刀版**（一组带 id/x/y/width/height 的轴对齐面板矩形）真实推断：
        面板邻接关系（Panel Relation）、折线（相邻面板共享边）、折叠连通性（刀版必须单连通）、
        每面板邻接度与尺寸分级、外轮廓（裁切线）总长。全部为轴对齐矩形求交 + 并查集/ BFS 连通分量，
        **纯确定性几何，不用 CV、不猜语义**。输入是矢量刀版（CAD 导出）而非栅格图，故与
        栅格图→面板的图像识别（Phase 47 起属 panel_recognition_engine）严格区分。

        诚实：角色分级仅作几何启发式（role_basis=geometric-heuristic），不谎称识别出"正面/背面"等
        语义角色；邻接/折线/连通/边长为精确可验证的几何事实。
        """
        if not panels:
            raise ValueError("至少需要一个面板")
        tol = max(float(tol), 0.0)
        min_fold = max(float(min_fold), tol)

        rects: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for i, p in enumerate(panels):
            try:
                pid = str(p.get("id", f"panel-{i}"))
                x, y = float(p["x"]), float(p["y"])
                w, h = float(p["width"]), float(p["height"])
            except (KeyError, TypeError, ValueError) as e:
                rejected.append({"index": i, "id": p.get("id") if isinstance(p, dict) else None,
                                 "reason": f"字段缺失或非法: {type(e).__name__}"})
                continue
            if w <= 0 or h <= 0:
                rejected.append({"index": i, "id": pid, "reason": "宽或高必须为正"})
                continue
            rects.append({"id": pid, "x1": x, "y1": y, "x2": x + w, "y2": y + h,
                          "width": w, "height": h, "area": w * h})

        if not rects:
            return {"status": "error", "implemented": True, "fabricated": False,
                    "reason": "无有效面板（全部被拒绝）", "rejected": rejected, "units": units}

        n = len(rects)

        def _overlap(a1: float, a2: float, b1: float, b2: float) -> float:
            return min(a2, b2) - max(a1, b1)

        adjacency: List[Dict[str, Any]] = []
        for i in range(n):
            for j in range(i + 1, n):
                A, B = rects[i], rects[j]
                # A 右边界贴 B 左边界（或反之）→ 竖直折线
                vertical = None
                if abs(A["x2"] - B["x1"]) <= tol:
                    vertical = (B["x1"], "vertical")
                elif abs(B["x2"] - A["x1"]) <= tol:
                    vertical = (A["x1"], "vertical")
                if vertical is not None:
                    ln = _overlap(A["y1"], A["y2"], B["y1"], B["y2"])
                    if ln > min_fold:
                        adjacency.append({"a": A["id"], "b": B["id"], "axis": "vertical",
                                          "fold_line": {"x": vertical[0],
                                                        "y1": max(A["y1"], B["y1"]),
                                                        "y2": min(A["y2"], B["y2"])},
                                          "shared_length": round(ln, 6)})
                        continue
                # A 下边界贴 B 上边界（或反之）→ 水平折线
                horizontal = None
                if abs(A["y2"] - B["y1"]) <= tol:
                    horizontal = (B["y1"], "horizontal")
                elif abs(B["y2"] - A["y1"]) <= tol:
                    horizontal = (A["y1"], "horizontal")
                if horizontal is not None:
                    ln = _overlap(A["x1"], A["x2"], B["x1"], B["x2"])
                    if ln > min_fold:
                        adjacency.append({"a": A["id"], "b": B["id"], "axis": "horizontal",
                                          "fold_line": {"y": horizontal[0],
                                                        "x1": max(A["x1"], B["x1"]),
                                                        "x2": min(A["x2"], B["x2"])},
                                          "shared_length": round(ln, 6)})

        # 邻接度 + 连通分量（BFS）
        adj_ids = {r["id"]: set() for r in rects}
        for e in adjacency:
            adj_ids[e["a"]].add(e["b"])
            adj_ids[e["b"]].add(e["a"])
        seen: set = set()
        groups: List[List[str]] = []
        for r in rects:
            if r["id"] in seen:
                continue
            comp: List[str] = []
            stack = [r["id"]]
            seen.add(r["id"])
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nb in adj_ids[cur]:
                    if nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            groups.append(sorted(comp))

        max_area = max(r["area"] for r in rects)
        panels_out: List[Dict[str, Any]] = []
        for r in rects:
            deg = len(adj_ids[r["id"]])
            primary = r["area"] >= 0.5 * max_area
            role_hint = ("major-face" if primary else "minor-flap") + \
                        ("/wide" if r["width"] >= r["height"] else "/tall")
            panels_out.append({
                "id": r["id"], "x": r["x1"], "y": r["y1"],
                "width": r["width"], "height": r["height"],
                "area": round(r["area"], 6), "fold_degree": deg,
                "size_class": "primary" if primary else "minor",
                "role_hint": role_hint, "role_basis": "geometric-heuristic",
            })

        perim_total = sum(2 * (r["width"] + r["height"]) for r in rects)
        shared_total = sum(e["shared_length"] for e in adjacency)
        boundary_length = perim_total - 2 * shared_total  # 共享边折叠后不计入外轮廓裁切线
        xs1 = min(r["x1"] for r in rects); ys1 = min(r["y1"] for r in rects)
        xs2 = max(r["x2"] for r in rects); ys2 = max(r["y2"] for r in rects)
        is_connected = len(groups) == 1

        return {
            "status": "ok" if is_connected else "disconnected",
            "implemented": True, "fabricated": False,
            "method": "axis-aligned rectangle shared-edge adjacency + connected-component (pure geometry, no CV)",
            "units": units, "tol": tol, "min_fold": min_fold,
            "panel_count": n, "panels": panels_out,
            "relation_count": len(adjacency), "adjacency": adjacency,
            "is_connected": is_connected, "group_count": len(groups), "groups": groups,
            "degrees": {r["id"]: len(adj_ids[r["id"]]) for r in rects},
            "fold_length_total": round(shared_total, 6),
            "cut_boundary_length": round(boundary_length, 6),
            "extent": {"x1": xs1, "y1": ys1, "x2": xs2, "y2": ys2,
                       "width": round(xs2 - xs1, 6), "height": round(ys2 - ys1, 6)},
            "total_panel_area": round(sum(r["area"] for r in rects), 6),
            "rejected": rejected,
            "note": "本方法处理矢量刀版(矩形面板)的 Panel Relation，不做任何图像解码（栅格图→面板由 panel_recognition_engine 承担）。role_hint 为几何启发式，非语义鉴定。",
        }


def _qr_verify_tables() -> None:
    """导入期算术自检：钉住 RS 分块表里**能被算术钉住的那一半**。

    QR 里只有 `_QR_BLOCKS` 不能由模块几何反算（总码字数/余比特/校正中心/格式与版本 BCH 全都
    可推，见 p57_qr_capacity_probe.txt 的逐版几何枚举）。抄错一行的后果不是"报错"而是
    **产出一张任何解码器都读不出的漂亮位图**——正是 §2 红线里最凶的假成功形制。故两道把关：
      ① 本函数（导入即炸）：逐行核 Σ块数×(数据+每块纠错) == 几何反算的总码字数、每块纠错数
         取值域、分块只允许 k / k+1 两档且少者在前、同版本数据容量严格 L>M>Q>H；
      ② L2/L4 第三方 OpenCV 逐 (版本×等级) 读回载荷逐字相等（钉住 ① 管不到的"是否真是
         ISO 公布的那一行"——读回失败即 RS 结构与规格表不符）。
    """
    bad: List[str] = []
    for ver in range(1, PackagingEngine.QR_MAX_VERSION + 1):
        total = PackagingEngine._QR_TOTAL_CODEWORDS[ver]
        caps: Dict[str, int] = {}
        for level in PackagingEngine._QR_EC_LEVELS:
            row = PackagingEngine._QR_BLOCKS.get((ver, level))
            if row is None:
                bad.append(f"({ver},{level}) 表缺行")
                continue
            ec, groups = row
            n = sum(c for c, _d in groups)
            dsum = sum(c * d for c, d in groups)
            if n * ec + dsum != total:
                bad.append(f"({ver},{level}) 和式 {n}×{ec}+{dsum} ≠ T={total}")
            if not (7 <= ec <= 34):
                bad.append(f"({ver},{level}) 每块纠错码字数 {ec} 越出 [7,34]")
            if not (1 <= n <= 45):
                bad.append(f"({ver},{level}) 块数 {n} 越出 [1,45]")
            ds = [d for _c, d in groups]
            if len(ds) > 2 or any(d <= 0 for d in ds):
                bad.append(f"({ver},{level}) 分块档数/长度为零 {groups}")
            elif len(ds) == 2 and ds[1] - ds[0] != 1:
                bad.append(f"({ver},{level}) 两档数据长度须为 k+1 且少者在前：{groups}")
            caps[level] = dsum
        seq = [caps.get(lv) for lv in PackagingEngine._QR_EC_LEVELS]
        if all(v is not None for v in seq) and not all(
                a > b for a, b in zip(seq, seq[1:])):
            bad.append(f"ver{ver} 数据容量须严格 L>M>Q>H：{seq}")
    if bad:
        raise RuntimeError(
            "QR 规格表导入自检失败（§2 红线：宁可导入即炸，绝不产出读不出的码）：\n  "
            + "\n  ".join(bad))


_qr_verify_tables()

if __name__ == "__main__":
    import tempfile
    eng = PackagingEngine()
    d = eng.dieline(100, 80, bleed=3, safe=5)
    assert d["trim_box"] == [0, 0, 100, 80]
    assert d["bleed_box"] == [-3, -3, 103, 83]
    assert d["areas"]["bleed_area"] == round(106 * 86, 3), d["areas"]
    # Phase 51 数据桥：dieline 的 panels 必须能被 panel-topology 直接消费，
    # 且外轮廓裁切线长必须等于成品面周长（可独立复算，非自报）。
    assert d["panel_count"] == 1 and d["panel_basis"] == "trim_face_only", d
    assert d["panels"][0]["width"] == 100 and d["panels"][0]["height"] == 80, d["panels"]
    _t = eng.analyze_panel_topology(d["panels"])
    assert _t["panel_count"] == 1 and _t["is_connected"] is True, _t
    assert _t["cut_boundary_length"] == 2 * (100 + 80), _t["cut_boundary_length"]
    assert _t["extent"]["width"] == 100 and _t["extent"]["height"] == 80, _t["extent"]
    imp = eng.imposition(die_width=50, die_height=50, count=100,
                         sheet_width=500, sheet_height=500, gap=0)
    assert imp["feasible"] and imp["n_up"] == 100 and imp["sheets_needed"] == 1, imp
    assert abs(imp["sheet_utilization_percent"] - 100) < 1e-6, imp
    imp2 = eng.imposition(30, 20, 250, 210, 297, gap=2)  # A4 拼 30x20
    assert imp2["feasible"] and imp2["sheets_needed"] >= 1
    # 真实 CSV 合并
    with tempfile.TemporaryDirectory() as tmp:
        csvp = Path(tmp) / "vdp.csv"
        csvp.write_text("name,sku,weight\n可乐,A001,330\n雪碧,A002,330\n芬达,A003\n",
                        encoding="utf-8")
        m = eng.variable_data_merge(csvp, field_map={"product": "name", "code": "sku",
                                                     "grams": "weight", "flavor": "flavor"})
        assert m["total_rows"] == 3 and m["merged_rows"] == 3, m
        assert m["records"][0]["variant"]["product"] == "可乐"
        assert m["records"][2]["variant"]["grams"] is None  # 第3行缺 weight → 留空不臆造
        assert "flavor" in m["missing_columns"], m["missing_columns"]  # 源无 flavor 列
        assert m["fabricated"] is False
    assert not hasattr(eng, "recognize_panels"), "栅格面板识别已下沉 panel_recognition_engine，本引擎不得保留占位"
    # ---- 真实 §46 Panel Relation / 刀版面板拓扑（纯几何，非 CV）----
    # 直插盒展开：糊口 + 4 面同高手身横排 + 上下各 2 插盖
    box = [
        {"id": "glue", "x": -15, "y": 0, "width": 15, "height": 100},
        {"id": "front", "x": 0, "y": 0, "width": 60, "height": 100},
        {"id": "right", "x": 60, "y": 0, "width": 40, "height": 100},
        {"id": "back", "x": 100, "y": 0, "width": 60, "height": 100},
        {"id": "left", "x": 160, "y": 0, "width": 40, "height": 100},
        {"id": "top_f", "x": 0, "y": 100, "width": 60, "height": 30},
        {"id": "top_b", "x": 100, "y": 100, "width": 60, "height": 30},
        {"id": "bot_f", "x": 0, "y": -30, "width": 60, "height": 30},
        {"id": "bot_b", "x": 100, "y": -30, "width": 60, "height": 30},
    ]
    topo = eng.analyze_panel_topology(box)
    assert topo["implemented"] is True and topo["fabricated"] is False, topo
    assert topo["panel_count"] == 9 and topo["relation_count"] == 8, (topo["panel_count"], topo["relation_count"])
    assert topo["is_connected"] and topo["group_count"] == 1, topo["groups"]
    deg = {p["id"]: p["fold_degree"] for p in topo["panels"]}
    assert deg["front"] == 4 and deg["glue"] == 1 and deg["right"] == 2, deg
    sz = {p["id"]: p["size_class"] for p in topo["panels"]}
    assert sz["top_f"] == "minor" and sz["front"] == "primary" and sz["right"] == "primary", sz
    assert topo["cut_boundary_length"] == 870, topo["cut_boundary_length"]
    # 断开的两半必须诚实报 disconnected，而非硬判连通
    two = eng.analyze_panel_topology([{"id": "a", "x": 0, "y": 0, "width": 10, "height": 10},
                                      {"id": "b", "x": 100, "y": 100, "width": 10, "height": 10}])
    assert two["is_connected"] is False and two["group_count"] == 2 and two["status"] == "disconnected", two
    # 非法面板入 rejected，不崩溃
    mixed = eng.analyze_panel_topology([{"id": "ok", "x": 0, "y": 0, "width": 10, "height": 10},
                                        {"id": "bad", "x": 0, "y": 0, "width": -5, "height": 10}])
    assert mixed["panel_count"] == 1 and len(mixed["rejected"]) == 1, mixed
    try:
        eng.analyze_panel_topology([])
        raise AssertionError("空面板应抛 ValueError")
    except ValueError:
        pass
    # ---- 真实 EAN-13 校验位（对照已知合法码）----
    assert eng._ean13_check_digit("590123412345") == "7"
    assert eng._ean13_check_digit("400638133393") == "1"
    assert eng._ean8_check_digit("9638507") == "4"  # 已知合法 EAN-8: 96385074
    with tempfile.TemporaryDirectory() as tmp:
        bc = eng.render_barcode("590123412345", symbology="EAN13",
                                output_path=str(Path(tmp) / "bc.png"))
        assert bc["implemented"] is True and bc["code"] == "5901234123457", bc
        assert bc["check_digit"] == "7"
        assert bc["module_count"] == 95 and len(bc["bit_pattern"]) == 95, bc["module_count"]
        assert bc["bit_pattern"].startswith("101") and bc["bit_pattern"].endswith("101")
        assert "01010" in bc["bit_pattern"]
        assert Path(bc["output_path"]).exists() and bc["bytes"] > 0
        bc8 = eng.render_barcode("9638507", symbology="EAN8",
                                 output_path=str(Path(tmp) / "bc8.png"))
        assert bc8["code"] == "96385074" and bc8["module_count"] == 67, bc8
        # 非法校验位必须被拒绝，而非静默伪造
        try:
            eng.render_barcode("5901234123450", symbology="EAN13")  # 校验位应为7
            raise AssertionError("非法校验位应抛 ValueError")
        except ValueError:
            pass
        # 未覆盖码制如实 needs_external，不伪造图像
        # （Phase 57 起 QR 已真实实现，故 honest-degradation 断言改用**仍未投递**的码制；
        #   Code-128 留守的理由见 agent_service 能力行 note 与本文件头注：107 项模式表不可几何
        #   推导，且本机 cv2.barcode 无编码器、第三方夹具 144 次试读未证得该码制 ⇒ 抄错索引永不可
        #   证伪（旧口径「本机无可运行的一维解码 oracle」已于 2026-10-11 被实测反驳），§2 红线不投。）
        for _sym in ("Code128", "DataMatrix", "PDF417"):
            _r = eng.render_barcode("ABC123", symbology=_sym)
            assert _r["implemented"] is False and _r["status"] == "needs_external", (_sym, _r)
        assert list(Path(tmp).glob("barcode_*")) == [] and list(Path(tmp).glob("qr_*")) == [], \
            "未实现码制绝不落盘"
        # ---- Phase 57：QR（ISO/IEC 18004）真实实现的结构不变式判据 ----
        # 判据口径：本块**自带**一套独立的 GF(256)/RS/BCH/几何实现（_t_ 前缀），
        # 与引擎内部实现零共享代码路径；故引擎抄错 RS 分块表、漏一维 BCH 校验、
        # 把定位图形错一位、或"位图与码字不符"都会当场红，而不是靠自报字段自证。
        # 与 L2/L4 的分工：这里钉**结构**（自研独立复算），那里钉**语义**
        # （第三方 OpenCV 逐字读回载荷）。两路都过才叫真实现。

        # ---- 判卷侧独立数学（GF(256) / BCH / RS syndrome / 功能图形几何）----
        #     这些函数**不读引擎的任何常量**，全部按 ISO/IEC 18004 自行成立；
        #     若与引擎共享一份表，则"抄错表"这件事在 L1 里就不可见（P49/P52 教训）。
        _T_EXP = [0] * 512
        _T_LOG = [0] * 256
        _x = 1
        for _i in range(255):                    # GF(256)，本原多项式 0x11D
            _T_EXP[_i] = _x
            _T_LOG[_x] = _i
            _x <<= 1
            if _x & 0x100:
                _x ^= 0x11D
        for _i in range(255, 512):
            _T_EXP[_i] = _T_EXP[_i - 255]

        def _t_mul(a, b):
            return 0 if (a == 0 or b == 0) else _T_EXP[_T_LOG[a] + _T_LOG[b]]

        def _t_bch_encode(data, gen, check_bits, xor_mask):
            """独立的多项式除法：返回 ((data<<check_bits)|余数) ^ xor_mask。"""
            rem = data << check_bits
            gd = gen.bit_length() - 1
            for i in range(rem.bit_length() - 1, gd - 1, -1):
                if (rem >> i) & 1:
                    rem ^= gen << (i - gd)
            return (((data << check_bits) | (rem & ((1 << check_bits) - 1))) ^ xor_mask)

        def _t_rs_syndromes(cw, ec_count):
            """按"首码字为最高次项"的多项式约定在 α^0..α^(ec-1) 处求值，全零才合法。"""
            out = []
            for i in range(ec_count):
                acc = 0
                for sym in cw:
                    acc = _t_mul(acc, _T_EXP[i]) ^ (sym & 0xFF)
                out.append(acc)
            return out

        def _t_fmt_pos_a(bit):
            if bit < 6:
                return (bit, 8)
            if bit == 6:
                return (7, 8)
            if bit == 7:
                return (8, 8)
            if bit == 8:
                return (8, 7)
            return (8, 14 - bit)

        def _t_fmt_pos_b(bit, side):
            """格式信息 B 副本位序 —— **判卷侧独立手抄 ISO/IEC 18004 图 24**，不取自引擎。

            bit0..7 沿**行 8** 自右向左（列 = side-1-bit），bit8..14 沿**列 8** 自下向上
            （行 = side-15+bit）。此处曾一度抄成引擎的 `(side-1-bit, 8) if bit<7 else
            (8, side-15+bit)`（把两段各 7/8 位**转置**到了另一条边），于是 `val_a==val_b==fmt_val`
            这条本该抓住摆放错误的判据变成橡皮图章——判卷侧抄了被测物的错，等于没有判据。
            教训：判卷侧常量/坐标必须独立成文，且须自带能抓住该错的负对照（见下方「转置负对照」）。
            """
            return (8, side - 1 - bit) if bit < 8 else (side - 15 + bit, 8)

        def _t_ver_pos(bit, side, copy):
            a, b = side - 11 + bit % 3, bit // 3
            return (b, a) if copy == 0 else (a, b)

        def _t_rs_gen_poly(ec_count):
            """g(x)=Π(x-α^i)，返回**降幂**系数表（首项 1）；判卷侧独立成立。"""
            g = [1]
            for i in range(ec_count):
                nxt = [0] * (len(g) + 1)
                for j, c in enumerate(g):
                    nxt[j] ^= c
                    nxt[j + 1] ^= _t_mul(c, _T_EXP[i])
                g = nxt
            return g

        def _t_rs_remainder(data, gen):
            """data·x^ec mod g(x)：LFSR 逐符号消元，返回 ec 个余数系数。"""
            ec = len(gen) - 1
            rem = [0] * ec
            for sym in data:
                factor = sym ^ rem[0]
                rem = rem[1:] + [0]
                for i in range(ec):
                    rem[i] ^= _t_mul(factor, gen[i + 1])
            return rem

        def _t_function_modules(ver, side):
            """逐模块**枚举**功能图形占据的坐标集合再取基数（不用面积公式，避免重复计数）。"""
            used = set()
            for (r0, c0) in ((0, 0), (0, side - 8), (side - 8, 0)):
                for r in range(r0, r0 + 8):
                    for c in range(c0, c0 + 8):
                        used.add((r, c))
            for i in range(8, side - 8):
                used.add((6, i))
                used.add((i, 6))
            if ver >= 2:
                n = 2 + ver // 7
                last = side - 7
                step = (last - 6) // (n - 1)
                step += step % 2
                centers = [6 + step * i for i in range(n - 1)] + [last]
                for cr in centers:
                    for cc in centers:
                        if (cr, cc) in {(6, 6), (6, last), (last, 6)}:
                            continue
                        for r in range(cr - 2, cr + 3):
                            for c in range(cc - 2, cc + 3):
                                used.add((r, c))
            for bit in range(15):
                used.add(_t_fmt_pos_a(bit))
                used.add(_t_fmt_pos_b(bit, side))
            used.add((side - 8, 8))
            if ver >= 7:
                for bit in range(18):
                    used.add(_t_ver_pos(bit, side, 0))
                    used.add(_t_ver_pos(bit, side, 1))
            return len(used)

        # ---- 判卷侧数学自证（KAT + 负对照）：尺子数不出 1 就没资格判 0 ----
        #   对账常量为 ISO/IEC 18004 已发表值（格式信息表 / 版本信息表），**不取自引擎**。
        assert _T_EXP[8] == 29 and _T_EXP[255] == _T_EXP[0] == 1, "GF(256) 指数表自证"
        assert _t_bch_encode(0b01000, 0x537, 10, 0x5412) == 0b111011111000100, "KAT: L/mask0"
        assert _t_bch_encode(0b00000, 0x537, 10, 0x5412) == 0b101010000010010, "KAT: M/mask0"
        assert _t_bch_encode(0b000111, 0x1F25, 12, 0) == 0b000111110010010100, "KAT: v7 版本信息"
        _g10 = _t_rs_gen_poly(10)
        assert _g10[0] == 1 and len(_g10) == 11, "RS 生成多项式次数自证"
        _probe_data = [(i * 37 + 11) & 0xFF for i in range(13)]
        _probe_cw = _probe_data + _t_rs_remainder(_probe_data, _g10)
        assert all(s == 0 for s in _t_rs_syndromes(_probe_cw, 10)), "判卷侧 RS 自检须为零"
        _broken = list(_probe_cw)
        _broken[3] ^= 0x5A
        assert any(s != 0 for s in _t_rs_syndromes(_broken, 10)), \
            "判卷侧 RS 自检必须能抓出坏码字（负对照；否则 syndrome 判据是空尺）"
        assert _t_function_modules(1, 21) == 21 * 21 - 26 * 8, "功能图形枚举须对得上 v1 已知容量"
        assert _t_function_modules(2, 25) == 25 * 25 - 44 * 8 - 7, "同上 v2（含 7 位余比特）"

        assert hasattr(eng, "render_qr"), \
            "QR 尚未真实实现：引擎缺 render_qr（Phase 57 RED 首跳，须以干净断言领红而非 TypeError）"
        payload = "https://zaojing.example/trace/9787111"
        qr = eng.render_barcode(payload, symbology="QR", scale=8, ec_level="M",
                                output_path=str(Path(tmp) / "qr.png"))
        assert qr["implemented"] is True and qr["status"] == "ok", qr
        assert qr["fabricated"] is False and qr["symbology"] == "QR", qr
        assert qr["code"] == payload, "QR 载荷必须逐字不变（不裁剪、不加前缀）"
        side, ver = qr["side"], qr["version"]
        assert 1 <= ver <= eng.QR_MAX_VERSION, ver
        assert side == 21 + 4 * (ver - 1), (side, ver)
        assert qr["module_count"] == side * side, qr["module_count"]
        assert qr["quiet_zone_modules"] >= 4, qr["quiet_zone_modules"]
        assert len(qr["matrix"]) == side and all(len(r) == side for r in qr["matrix"])
        assert set("".join(qr["matrix"])) <= {"0", "1"}
        assert qr["bit_pattern"] == "".join(qr["matrix"]), "位串必须逐行等于模块矩阵"
        assert qr["data_codewords"] + qr["ec_codewords"] == qr["total_codewords"], qr

        # (1) 功能图形几何：三个定位图形 + 分隔符 + 两条定时图形，逐模块核
        def _cell(rr, cc):
            return qr["matrix"][rr][cc]

        for (r0, c0) in ((0, 0), (0, side - 7), (side - 7, 0)):
            for i in range(7):
                for j in range(7):
                    want = "1" if (i in (0, 6) or j in (0, 6) or (2 <= i <= 4 and 2 <= j <= 4)) else "0"
                    assert _cell(r0 + i, c0 + j) == want, (r0, c0, i, j, want)
        for i in range(8):
            for (rr, cc) in ((7, i), (i, 7), (7, side - 1 - i), (side - 1 - i, 7),
                              (side - 8, i)):
                assert _cell(rr, cc) == "0", ("separator", rr, cc)
        for i in range(8, side - 8):
            want = "1" if i % 2 == 0 else "0"
            assert _cell(6, i) == want, ("timing-h", i, _cell(6, i))
            assert _cell(i, 6) == want, ("timing-v", i, _cell(i, 6))
        assert _cell(side - 8, 8) == "1", "固定黑模块 (dark module) 必须在 (4v+9, 8)"

        # (2) 校正图形位置：由几何**独立推导**（非抄表），逐中心核 5x5
        if ver >= 2:
            n = 2 + ver // 7
            last = side - 7
            step = (last - 6) // (n - 1)
            step += step % 2
            centers = [6 + step * i for i in range(n - 1)] + [last]
            assert qr["alignment_centers"] == centers, (qr["alignment_centers"], centers)
            for cr in centers:
                for cc in centers:
                    if (cr, cc) in {(6, 6), (6, last), (last, 6)}:
                        continue  # 与定位图形重叠的三个不放置
                    for i in range(-2, 3):
                        for j in range(-2, 3):
                            want = "1" if max(abs(i), abs(j)) != 1 else "0"
                            assert _cell(cr + i, cc + j) == want, ("align", cr, cc, i, j)

        # (3) 格式信息：两副本读回 + BCH(15,5) 独立复算 + 解出申报的 (ec, mask)
        #     位序（ISO/IEC 18004 图 24）：bit0 为最低位；A 副本绕左上（列 8 向下、跳过定时行 6，
        #     再沿行 8 向左、跳过定时列 6）；B 副本沿**行 8** 自右下角向左 8 位（bit0..7）、
        #     再沿**列 8** 自下向上 7 位（bit8..14），两段与 A 副本同向、**不是转置**。
        fbits = qr["format_bits"]
        assert len(fbits) == 15 and set(fbits) <= {"0", "1"}
        readA = "".join(_cell(*_t_fmt_pos_a(i)) for i in range(15))
        readB = "".join(_cell(*_t_fmt_pos_b(i, side)) for i in range(15))
        val_a = sum(int(readA[i]) << i for i in range(15))
        val_b = sum(int(readB[i]) << i for i in range(15))
        fmt_val = int(fbits, 2)
        ec_bit = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}[qr["ec_level"]]
        want5 = (ec_bit << 3) | qr["mask_pattern"]
        assert val_a == val_b == fmt_val, ("格式信息两副本读回必须与申报值全等", readA, readB, fbits)
        assert _t_bch_encode(want5, 0x537, 10, 0x5412) == fmt_val, "格式信息 BCH 复算不符"

        # (3b) 转置负对照：本判据**必须**能抓住"把 B 副本按 i<->14-i 反转摆放"这一
        #     曾真实发生过的缺陷。判卷侧若照抄被测物的位序，则两副本永远互等，
        #     `val_a==val_b` 退化成橡皮图章。这里手工造一张错图，要求判据当场变红；
        #     数不出 1 的尺子没资格判 0。
        _badB = [list(r) for r in qr["matrix"]]
        for b in range(15):
            _v = _cell(*_t_fmt_pos_a(b))
            _r, _c = (side - 1 - b, 8) if b < 7 else (8, side - 15 + b)  # 旧的错位序
            _badB[_r][_c] = _v
        _rb = "".join(_badB[r][c] for (r, c) in map(lambda i: _t_fmt_pos_b(i, side),
                                                    range(15)))
        assert _rb != readA, ("转置负对照失效：判卷侧抓不住 B 副本反转摆放", readA, _rb)
        assert _rb != fbits or readA != fbits, "转置负对照必须至少翻转一侧读回"

        # (4) 版本信息（v>=7）：BCH(18,6) 独立复算 + 两副本读回
        if ver >= 7:
            vbits = qr["version_info_bits"]
            assert len(vbits) == 18 and set(vbits) <= {"0", "1"}
            got1 = "".join(_cell(*_t_ver_pos(i, side, 0)) for i in range(18))
            got2 = "".join(_cell(*_t_ver_pos(i, side, 1)) for i in range(18))
            val1 = sum(int(got1[i]) << i for i in range(18))
            v15 = int(vbits, 2)
            assert got1 == got2, ("版本信息两副本必须互等", got1, got2)
            assert val1 == v15 == _t_bch_encode(ver, 0x1F25, 12, 0), \
                ("版本信息读回/BCH 复算不符", got1, vbits)
        else:
            assert qr["version_info_bits"] is None

        # (5) Reed-Solomon：按各自分块**独立求 syndrome**，必须全零
        blocks = qr["blocks"]
        stream = qr["codewords"]
        assert len(stream) == qr["total_codewords"]
        per = [b["data"] * b["count"] + b["ec"] * b["count"] for b in blocks]
        assert sum(per) == len(stream), (per, len(stream))
        # 反交织：数据与 EC 各自按"块序轮询第 j 个码字"的标准交织规则独立复现
        sizes_d, sizes_e = [], []
        for b in blocks:
            sizes_d += [b["data"]] * b["count"]
            sizes_e += [b["ec"]] * b["count"]
        nb = len(sizes_d)
        dblocks = [[] for _ in range(nb)]
        k = 0
        for j in range(max(sizes_d)):
            for i in range(nb):
                if j < sizes_d[i]:
                    dblocks[i].append(stream[k]); k += 1
        ec_stream = stream[k:]
        assert len(ec_stream) == sum(sizes_e)
        eidx = 0
        for j in range(max(sizes_e)):
            for i in range(nb):
                if j < sizes_e[i]:
                    dblocks[i].append(ec_stream[eidx]); eidx += 1
        assert all(len(d) == sizes_d[i] + sizes_e[i] for i, d in enumerate(dblocks))
        for i, cw in enumerate(dblocks):
            synd = _t_rs_syndromes(cw, sizes_e[i])
            assert all(s == 0 for s in synd), ("RS 校验子必须全零", i, sizes_e[i], synd[:4])
        assert sum(sizes_d) == qr["data_codewords"] and sum(sizes_e) == qr["ec_codewords"]
        # 数据码字流必须真装着载荷（防"位图漂亮但码字是空壳"）：把各块数据段按块序
        # 重新拼回原始位流（QR 的分块是把数据流**连续切段**，交织只发生在写模块时），
        # 再断言 Byte 模式下的载荷字节序列逐字出现在其中。
        _data_stream = "".join(format(c, "08b") for i, d in enumerate(dblocks)
                               for c in d[:sizes_d[i]])
        _payload_bin = "".join(format(b, "08b") for b in payload.encode("utf-8"))
        assert _payload_bin in _data_stream, "Byte 模式载荷必须逐字出现在数据码字流里"

        # (6) 码字容量恒等式：总码字数必须等于**由模块几何反算**的可用码字数
        #     （不抄规格表的总码字列，独立由 size 扣功能图形 + remainder 反算）
        used = _t_function_modules(ver, side)
        assert (side * side - used - qr["remainder_bits"]) // 8 == qr["total_codewords"], \
            ("几何反算码字数与实现不符", (side * side - used - qr["remainder_bits"]) // 8,
             qr["total_codewords"])
        assert (side * side - used - qr["remainder_bits"]) % 8 == 0
        # v1..v6 另有判卷侧**手算硬锚**（26/44/70/100/134/172，由上式逐版核过）；
        # v>=7 的语义锚由 L2/L4 的第三方解码承担（判卷侧不重复引擎的表）。
        _anchor = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172}
        if ver in _anchor:
            assert qr["total_codewords"] == _anchor[ver], \
                ("总码字数与判卷侧手算锚不符", ver, qr["total_codewords"], _anchor[ver])

        # (7) 真像素逐模块复扫 + PNG 魔数 + 尺寸
        from PIL import Image as _QImg
        raw = Path(qr["output_path"]).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n", "必须是真 PNG 魔数"
        with _QImg.open(qr["output_path"]) as im:
            g = im.convert("L")
            assert g.size == (qr["width_px"], qr["height_px"]), (g.size, qr)
            assert qr["width_px"] == (side + 2 * qr["quiet_zone_modules"]) * qr["scale"]
            px = g.load()
            sc = qr["scale"]
            qz = qr["quiet_zone_modules"]
            for rr in range(side):
                for cc in range(side):
                    cx = (qz + cc) * sc + sc // 2
                    cy = (qz + rr) * sc + sc // 2
                    dark = px[cx, cy] < 128
                    assert int(dark) == int(qr["matrix"][rr][cc]), ("像素与模块不符", rr, cc)
            for yy in range(0, g.size[1], max(1, sc // 2)):
                for xx in range(0, qz * sc):
                    assert px[xx, yy] > 128, "静区必须全白"
        assert qr["bytes"] == len(raw) > 0

        # (8) 版本自动最小化：同样载荷在 L 级下不得选到更大的版本
        qrL = eng.render_barcode(payload, symbology="QR", scale=4, ec_level="L",
                                 output_path=str(Path(tmp) / "qrL.png"))
        assert qrL["version"] <= qr["version"], (qrL["version"], qr["version"])
        assert qrL["data_codewords"] >= qr["data_codewords"]

        # (9) 三形诚实拒绝：空载荷 / 非法 EC 级 / 超已实现容量 —— 一律抛错且绝不落盘
        for _n, (_kw, _why) in enumerate((({"code": ""}, "空载荷"),
                                          ({"code": "ZAOJING", "ec_level": "X"}, "非法 EC 级别"),
                                          ({"code": "A" * 4000}, "超出已实现版本范围"))):
            _a = dict(_kw)
            _a.setdefault("symbology", "QR")
            _a.setdefault("scale", 2)
            _out = str(Path(tmp) / f"bad_{_n}.png")
            try:
                eng.render_barcode(output_path=_out, **_a)
                raise AssertionError(f"{_why} 应抛 ValueError 而不是伪造图像")
            except ValueError as _e:
                assert str(_e), _why
                assert not Path(_out).exists(), f"{_why} 不得落盘"
        assert qr["implemented_version_range"] == [1, eng.QR_MAX_VERSION]
        assert qr["mode"] in ("Numeric", "Alphanumeric", "Byte"), qr["mode"]
        _num = eng.render_barcode("8800123456789", symbology="QR", scale=4, ec_level="M",
                                  output_path=str(Path(tmp) / "qr_num.png"))
        assert _num["mode"] == "Numeric", "纯数字载荷须走 Numeric 模式（容量最优）"
        _al = eng.render_barcode("ZAOJING-123 $%*", symbology="QR", scale=4, ec_level="M",
                                 output_path=str(Path(tmp) / "qr_al.png"))
        assert _al["mode"] == "Alphanumeric", "字符集全落在表 A 须走 Alphanumeric"
        _by = eng.render_barcode("造境包装", symbology="QR", scale=4, ec_level="M",
                                 output_path=str(Path(tmp) / "qr_by.png"))
        assert _by["mode"] == "Byte", "非表 A 字符（含中文 UTF-8）须走 Byte 模式"
        # 掩码必须真做过罚分选择：8 个候选里选罚分最小者（由引擎申报、判卷侧复核其合法性）
        assert 0 <= qr["mask_pattern"] <= 7
        assert qr["mask_penalty"] == min(qr["mask_penalties"]), \
            ("必须选罚分最小掩码", qr["mask_penalty"], qr["mask_penalties"])
        assert len(qr["mask_penalties"]) == 8
    try:
        eng.dieline(-1, 10)
        raise AssertionError("负尺寸应抛 ValueError")
    except ValueError:
        pass
    print("[PASS] packaging engine self-test  dieline_ok imposition=%dsheet vdp_rows=%d "
          "barcode_real=EAN13(95mod)/EAN8(67mod)/QR(v%d,%dmod,rs0) "
          "honest_unimpl=panel+Code128"
          % (imp["sheets_needed"], m["merged_rows"], qr["version"], qr["module_count"]))
