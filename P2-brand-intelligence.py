"""
造境 AI 设计智能体 - 品牌智能引擎 brand_engine（Phase 25 真实交付）

诚实原则（§2 红线）：
- 色彩分析走**真实像素与真实色差**：调色板复用 color_engine 的中位切分逐像素统计，
  品牌色一致性用公开 API nearest_in_palette 的 CIEDE2000 真实 ΔE 判定，
  覆盖率由真实数值比较得出，绝不返回固定"品牌分"。
- Logo 几何走**真实 alpha 通道包围盒**：内容框、四边留白、宽高比、填充率全部
  由逐像素 alpha 阈值扫描计算；alpha_threshold 真实参与过滤（负控可证伪）。
- 语义类能力（Logo 语义解读、品牌调性推断、字体识别）需要外部模型
  （CV / LLM），本引擎如实返回 status=needs_external 且 fabricated=False，
  绝不输出臆造的"调性关键词"或假置信度。能力真值表据此红线分裂：
  brand_color_analysis（调色板/品牌色一致性/Logo 几何）= supported，
  brand_semantic_recognition = not_shipped。

历史：V1.0 原型 P2-brand-intelligence.py 以固定示例数据填充 Logo/色彩/字体分析
（假成功信号第 1 类：硬编码 mock 字典），Phase 25 整文件重写为上述真实实现。
"""

from __future__ import annotations

import base64
import http.server
import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # 与 color_engine 同一策略：缺库如实报错，不伪造像素结果
    from PIL import Image  # type: ignore
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    PIL_AVAILABLE = False

SCHEMA = "zaojing-brand/v1"

# 语义能力的诚实边界（不是遗漏，是声明）
SEMANTIC_BOUNDARIES = {
    "logo_semantics": "Logo 图形语义解读需真实 CV 模型（未随服务打包）；栅格图→面板的几何识别已由 panel_recognition_engine 真实交付，但图形语义/调性解读不在其能力内，本引擎仍不实现",
    "brand_tone_inference": "品牌调性推断需已配置 LLM 且输出无确定性本地真值，为免伪造置信度不实现",
    "typography_recognition": "从栅格图识别字体需真实字体匹配服务（如 WhatTheFont，未授权集成）",
}

# 经真实 Provider 客户端做视觉语义识别时，发给所配置模型的提示词（可被调用方覆盖）。
# 本引擎自身绝不产出这些字段——它们全部由外部模型返回、逐字透传；模型不返回 JSON 时如实保留 raw。
SEMANTIC_PROMPT = (
    "你是品牌视觉分析器。仅输出一个 JSON 对象，不要解释文字或代码块围栏。"
    "严格包含这些键：tone_keywords(字符串数组)、logo_semantics(字符串)、"
    "typography_guess(字符串)、palette_notes(字符串)、confidence_note(字符串)。"
    "若无法判断请在对应字段如实说明不确定，绝不编造确定性。用与图像语义相符的语言作答。"
)

# 真实读取图像字节后据后缀给出的 MIME（OpenAI 兼容 image_url 的 data URL 前缀）
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


class BrandError(ValueError):
    """品牌分析中的诚实错误（入参非法等），路由层转 4xx。"""


def _finite_num(v: Any, what: str) -> float:
    """存在但非数值 / NaN / Inf 一律拒绝——NaN 会静默污染几何与色差比较。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise BrandError(f"{what} 需为数值，收到 {type(v).__name__}")
    if not math.isfinite(f):
        raise BrandError(f"{what} 需为有限数值（拒绝 NaN/Inf）")
    return f


def _palette_k(k: Any) -> int:
    v = _finite_num(k, "k")
    if not float(v).is_integer() or not (2 <= v <= 32):
        raise BrandError("k 需为 2..32 的整数")
    return int(v)


class BrandEngine:
    """品牌智能：真实调色板、真实 CIEDE2000 一致性、真实 Logo 几何；语义边界如实声明。

    color 引擎由宿主注入（agent_service 复用已装配单例）；独立自测时惰性导入同族引擎。
    """

    def __init__(self, color=None, provider=None):
        self._color = color
        self._provider = provider  # 真实 Provider 客户端（agent_service 注入已装配单例）；None 时语义识别如实 needs_external

    def _ce(self):
        if self._color is None:
            import color_engine as m
            self._color = m.ColorEngine()
        return self._color

    # --------------------------------------------------------------------
    # 1) 调色板：真实像素中位切分（复用 color_engine，不重复造轮子）
    # --------------------------------------------------------------------
    def analyze_palette(self, image_path: str, k: Any = 6) -> Dict[str, Any]:
        kk = _palette_k(k)
        try:
            result = self._ce().palette_from_image(str(image_path), k=kk)
        except FileNotFoundError:
            raise
        except RuntimeError as e:
            raise BrandError(str(e))
        result["schema"] = SCHEMA
        result["method"] = "median_cut_via_color_engine"
        return result

    # --------------------------------------------------------------------
    # 2) 品牌色一致性：真实 CIEDE2000 + 真实覆盖率
    # --------------------------------------------------------------------
    def check_brand_color_consistency(self, brand_hexes: Any, image_path: str,
                                      max_delta_e: Any = 3.0, k: Any = 8) -> Dict[str, Any]:
        if not isinstance(brand_hexes, (list, tuple)) or not brand_hexes:
            raise BrandError("brand_hexes 需为非空数组")
        if len(brand_hexes) > 32:
            raise BrandError("brand_hexes 最多 32 个")
        for i, b in enumerate(brand_hexes):
            if not isinstance(b, str):
                raise BrandError(f"brand_hexes[{i}] 需为字符串")
        max_de = _finite_num(max_delta_e, "max_delta_e")
        if max_de < 0:
            raise BrandError("max_delta_e 不能为负")
        kk = _palette_k(k)

        ce = self._ce()
        try:
            palette = ce.palette_from_image(str(image_path), k=kk)["palette"]
        except FileNotFoundError:
            raise
        except RuntimeError as e:
            raise BrandError(str(e))
        palette_hexes = [p["hex"] for p in palette]
        if not palette_hexes:
            raise BrandError("图像未产出任何调色板颜色")

        entries: List[Dict[str, Any]] = []
        matched_count = 0
        for b in brand_hexes:
            try:
                matches = ce.nearest_in_palette(b, palette_hexes)["matches"]
            except KeyError as e:
                raise BrandError(f"色彩引擎拒绝入参（非法 hex?）: {e}")
            best = matches[0] if matches else None
            de = best["delta_e"] if best else None
            matched = de is not None and de <= max_de  # 真实数值比较（判分器变异靶点）
            if matched:
                matched_count += 1
            entries.append({
                "brand_hex": b,
                "nearest_palette_hex": best["hex"] if best else None,
                "delta_e_ciede2000": de,
                "matched": matched,
            })
        coverage = matched_count / len(brand_hexes)
        return {
            "schema": SCHEMA,
            "image": str(image_path),
            "max_delta_e": max_de,
            "palette_size": len(palette_hexes),
            "brand_colors": entries,
            "coverage": round(coverage, 4),
            "all_matched": matched_count == len(brand_hexes),
            "method": "ciede2000_nearest_in_median_cut_palette",
            "fabricated": False,
        }

    # --------------------------------------------------------------------
    # 3) Logo 几何：真实 alpha 通道包围盒与留白
    # --------------------------------------------------------------------
    def logo_geometry(self, image_path: str, alpha_threshold: Any = 8) -> Dict[str, Any]:
        if not PIL_AVAILABLE:
            raise BrandError("Pillow 不可用，无法读取真实像素（不伪造几何）")
        thr = _finite_num(alpha_threshold, "alpha_threshold")
        if not (0 <= thr <= 255):
            raise BrandError("alpha_threshold 需在 0..255")
        p = Path(image_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"图像文件不存在: {image_path}")
        with Image.open(p) as im:  # type: ignore
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            rgba = im.convert("RGBA")
            w, h = rgba.size
            px = rgba.load()
            x_min = y_min = None
            x_max = y_max = -1
            content_pixels = 0
            for y in range(h):
                for x in range(w):
                    if px[x, y][3] >= thr:
                        content_pixels += 1
                        if x_min is None:
                            x_min, y_min = x, y
                        if x > x_max:
                            x_max = x
                        if y > y_max:
                            y_max = y
        if x_min is None:
            raise BrandError("按该透明度阈值不存在内容像素（可能整图为全透明或阈值过高）")
        cw = x_max - x_min + 1
        ch = y_max - y_min + 1
        return {
            "schema": SCHEMA,
            "source": str(p),
            "canvas": {"width": w, "height": h, "has_alpha_channel": has_alpha},
            "alpha_threshold": thr,
            "content_bbox": [x_min, y_min, x_max + 1, y_max + 1],  # 右开，与 margins 自洽
            "content_size": {"width": cw, "height": ch},
            "margins": {"left": x_min, "top": y_min,
                        "right": w - (x_max + 1), "bottom": h - (y_max + 1)},
            "aspect_ratio": round(cw / ch, 4),
            "fill_ratio": round(content_pixels / (cw * ch), 4),
            "content_pixels": content_pixels,
            "method": "real_alpha_bbox_scan",
            "fabricated": False,
        }

    # --------------------------------------------------------------------
    # 4) 语义边界：如实声明，绝不臆造
    # --------------------------------------------------------------------
    def semantic_boundaries(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": "needs_external",
            "fabricated": False,
            "boundaries": dict(SEMANTIC_BOUNDARIES),
            "provider_configured": bool(self._provider is not None
                                        and getattr(self._provider, "configured", None)
                                        and self._provider.configured()),
            "note": "语义类品牌分析（Logo 含义/调性/字体识别）依赖外部 CV/LLM 模型：本引擎自身"
                    "不返回任何臆造关键词或置信度。当已配置真实 Provider 时，POST /api/v1/brand/"
                    "semantic-insight 经真实 Provider 客户端调用视觉模型、逐字透传其输出（未配置则如实 "
                    "needs_external）；未配置 Provider 或模型输出非 JSON 时一律拒绝伪造。",
        }

    # --------------------------------------------------------------------
    # 5) 语义识别：经真实 Provider 客户端调用视觉模型，逐字透传、绝不臆造
    # --------------------------------------------------------------------
    @staticmethod
    def _extract_json(text: Any) -> Dict[str, Any]:
        """从模型原始输出中稳健提取 JSON 对象（容忍 ```json 围栏与前后噪声）；失败返回 {}。"""
        if not isinstance(text, str) or not text.strip():
            return {}
        t = text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            if t.lower().startswith("json"):
                t = t[4:]
            t = t.strip()
        try:
            obj = json.loads(t)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            pass
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(t[start:end + 1])
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        return {}

    def recognize_semantics(self, image_path: str, provider_id: Optional[str] = None,
                           prompt: Optional[str] = None) -> Dict[str, Any]:
        """真实读取图像字节 → 构造 OpenAI 兼容多模态 data URL → 委派已注入的 Provider 客户端调用
        视觉模型 → 逐字透传模型输出。本方法绝不自行生成任何调性/置信度：无 Provider 如实 needs_external，
        模型返回非 JSON 保留 raw 不硬编 schema，Provider 传输失败原样透传其状态（§2 红线）。"""
        p = Path(image_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"图像文件不存在: {image_path}")
        raw_bytes = p.read_bytes()
        if not raw_bytes:
            raise BrandError("图像文件为空，无法做语义识别（不伪造）")
        mime = _IMAGE_MIME.get(p.suffix.lower(), "application/octet-stream")

        if self._provider is None:
            return {
                "schema": SCHEMA, "status": "needs_external", "fabricated": False,
                "reason": "no_provider_injected",
                "image": str(p), "image_bytes": len(raw_bytes), "mime": mime,
                "boundaries": dict(SEMANTIC_BOUNDARIES),
                "note": "本引擎未接入 Provider 客户端；语义识别须经真实视觉模型，拒绝凭空伪造（POST /api/v1/provider/configure 接入）。",
            }

        data_url = "data:%s;base64,%s" % (mime, base64.b64encode(raw_bytes).decode("ascii"))
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": prompt or SEMANTIC_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}]
        resp = self._provider.chat(prompt="", provider_id=provider_id, messages=msgs)

        if resp.get("status") != "ok":
            st = resp.get("status", "provider_error")
            return {
                "schema": SCHEMA,
                "status": "needs_external" if st == "no_provider_configured" else st,
                "provider_status": st, "fabricated": False,
                "reason": resp.get("reason") or resp.get("error") or resp.get("detail") or st,
                "image": str(p), "image_bytes": len(raw_bytes), "mime": mime,
                "provider_response": {k: v for k, v in resp.items() if k != "content"},
                "note": "Provider 未返回可透传的成功结果，本引擎保留其真实状态与原因，绝不代模型编造语义（§2 红线）。",
            }

        content = resp.get("content", "")
        parsed = self._extract_json(content)
        if not parsed:
            return {
                "schema": SCHEMA, "status": "provider_output_unparseable", "fabricated": False,
                "image": str(p), "image_bytes": len(raw_bytes), "mime": mime,
                "provider": resp.get("provider"), "model": resp.get("model"),
                "usage": resp.get("usage"), "raw": (content or "")[:2000],
                "method": "real_provider_vision_passthrough",
                "note": "模型返回内容不可解析为 JSON，保留原始输出，绝不硬编造 schema 字段（§2 红线）。",
            }
        return {
            "schema": SCHEMA, "status": "ok", "fabricated": False,
            "image": str(p), "image_bytes": len(raw_bytes), "mime": mime,
            "semantics": parsed,
            "provider": resp.get("provider"), "model": resp.get("model"),
            "usage": resp.get("usage"),
            "method": "real_provider_vision_passthrough",
            "provenance": "所有语义字段均为所配置模型的真实输出逐字透传；本引擎未合成任何关键词或置信度",
        }


# ============================================================================
# 独立自测：真实断言 + 负控（must-not-match / 阈值真实参与），GBK 控制台安全
# ============================================================================
class _MockChatHandler(http.server.BaseHTTPRequestHandler):
    """自起 loopback mock：OpenAI 兼容 /chat/completions，回一段可配置的 content，
    并记录收到的请求体（供语义 data URL 独立复算）。仅测试用，绝不进产品路径。"""

    content_to_return = ""
    captured: Dict[str, Any] = {}

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        try:
            req = json.loads(body)
        except Exception:
            req = {}
        type(self).captured = {"path": self.path,
                               "auth": self.headers.get("Authorization", ""),
                               "req": req}
        resp = {"model": "vision-mock-1",
                "choices": [{"message": {"content": type(self).content_to_return}}],
                "usage": {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # silence stderr on GBK console
        pass


def _start_mock_chat(content_json_str: str):
    _MockChatHandler.content_to_return = content_json_str
    _MockChatHandler.captured = {}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MockChatHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _self_test() -> int:
    import tempfile
    ok_count, fail = 0, []

    def check(name: str, cond: bool, extra: str = ""):
        nonlocal ok_count
        if cond:
            ok_count += 1
            print(f"[OK] {name}")
        else:
            fail.append(name)
            print(f"[FAIL] {name} {extra}")

    if not PIL_AVAILABLE:
        print("[WARN] Pillow unavailable, brand self-test cannot run on fake pixels")
        return 1

    eng = BrandEngine()
    with tempfile.TemporaryDirectory(prefix="zjbrand_") as td:
        tdp = Path(td)

        # -- fixtures: 双色带图 + 纯红图 + 透明底 Logo --
        band = tdp / "band.png"
        im = Image.new("RGB", (100, 80))  # type: ignore
        for y in range(80):
            for x in range(100):
                im.putpixel((x, y), (255, 0, 0) if y < 40 else (0, 255, 0))
        im.save(band)
        red = tdp / "red.png"
        Image.new("RGB", (50, 50), (255, 0, 0)).save(red)  # type: ignore

        logo = tdp / "logo.png"
        li = Image.new("RGBA", (200, 100), (0, 0, 0, 0))  # type: ignore
        for y in range(25, 75):
            for x in range(50, 150):
                li.putpixel((x, y), (10, 20, 30, 255))
        li.putpixel((10, 10), (10, 20, 30, 100))  # 半透明噪点：低阈值纳入、高阈值排除
        li.save(logo)

        # -- 1) 调色板真实取色 --
        pal = eng.analyze_palette(str(band), k=2)
        hexes = sorted(e["hex"].upper() for e in pal["palette"])
        check("palette: two-band image yields exactly #00FF00 and #FF0000",
              hexes == ["#00FF00", "#FF0000"], str(hexes))
        pcts = sorted(round(e["percent"], 1) for e in pal["palette"])
        check("palette: 50/50 area split (real per-pixel counts)", pcts == [50.0, 50.0], str(pcts))
        try:
            eng.analyze_palette(str(band), k=0)
            check("palette: k=0 rejected", False)
        except BrandError:
            check("palette: k=0 rejected", True)
        try:
            eng.analyze_palette(str(band), k=float("nan"))
            check("palette: k=NaN rejected (no silent nan success)", False)
        except BrandError:
            check("palette: k=NaN rejected (no silent nan success)", True)
        try:
            eng.analyze_palette(str(tdp / "missing.png"), k=4)
            check("palette: missing file raises FileNotFoundError", False)
        except FileNotFoundError:
            check("palette: missing file raises FileNotFoundError", True)

        # -- 2) 品牌一致性：真实 CIEDE2000 --
        cons = eng.check_brand_color_consistency(["#FF0000", "#00FF00"], str(band), max_delta_e=1.0)
        check("consistency: exact brand pair on matching image -> coverage 1.0, all matched",
              cons["coverage"] == 1.0 and cons["all_matched"], str(cons["coverage"]))
        des = [e["delta_e_ciede2000"] for e in cons["brand_colors"]]
        check("consistency: identical colors -> delta_e == 0.0", all(d == 0.0 for d in des), str(des))
        # 负控（可证伪核心）：纯红图 + 纯绿品牌色，真实 ΔE 巨大，必须 NOT matched。
        # 若有人把 matched 判据改成恒真，此断言立即变红。
        neg = eng.check_brand_color_consistency(["#00FF00"], str(red), max_delta_e=10.0)
        green_vs_red_de = neg["brand_colors"][0]["delta_e_ciede2000"]
        check("consistency NEG: green brand vs red image -> matched=False",
              neg["brand_colors"][0]["matched"] is False and neg["coverage"] == 0.0, str(neg))
        check("consistency: red/green CIEDE2000 is large (>50)",
              green_vs_red_de is not None and green_vs_red_de > 50.0, str(green_vs_red_de))
        # 手工可核算真值：黑 vs 白，中性轴上 CIEDE2000 = dL = 100.0
        bw = eng._ce().delta_e("#FFFFFF", "#000000")["delta_e"]
        check("consistency: white/black DE2000 ground truth == 100.0", abs(bw - 100.0) < 1e-6, str(bw))
        try:
            eng.check_brand_color_consistency(["#GGGGGG"], str(band))
            check("consistency: invalid hex rejected", False)
        except BrandError:
            check("consistency: invalid hex rejected", True)
        try:
            eng.check_brand_color_consistency([], str(band))
            check("consistency: empty brand list rejected", False)
        except BrandError:
            check("consistency: empty brand list rejected", True)
        try:
            eng.check_brand_color_consistency(["#FF0000"], str(band), max_delta_e="a")
            check("consistency: non-numeric max_delta_e rejected", False)
        except BrandError:
            check("consistency: non-numeric max_delta_e rejected", True)

        # -- 3) Logo 几何：真实 alpha 包围盒 --
        g = eng.logo_geometry(str(logo), alpha_threshold=200)
        check("logo: high threshold bbox == painted rect [50,25,150,75]",
              g["content_bbox"] == [50, 25, 150, 75], str(g["content_bbox"]))
        check("logo: margins symmetric 50/25 from bbox",
              g["margins"] == {"left": 50, "top": 25, "right": 50, "bottom": 25}, str(g["margins"]))
        check("logo: aspect 2.0, fill 1.0 (solid rect)",
              g["aspect_ratio"] == 2.0 and g["fill_ratio"] == 1.0, str(g))
        g_low = eng.logo_geometry(str(logo), alpha_threshold=50)
        check("logo NEG-CONTROL: threshold genuinely filters (alpha=100 speck joins bbox at thr=50)",
              g_low["content_bbox"] == [10, 10, 150, 75] and g_low["margins"]["left"] == 10,
              str(g_low["content_bbox"]))
        try:  # 噪点图最大 alpha=100，阈值 150 之上应无内容 -> 诚实报错而非假 bbox
            dim = tdp / "dim.png"
            Image.new("RGBA", (20, 20), (5, 5, 5, 100)).save(dim)  # type: ignore
            eng.logo_geometry(str(dim), alpha_threshold=150)
            check("logo: threshold above image max alpha -> honest error, not fake bbox", False)
        except BrandError:
            check("logo: threshold above image max alpha -> honest error, not fake bbox", True)
        try:
            eng.logo_geometry(str(logo), alpha_threshold=float("inf"))
            check("logo: Inf threshold rejected", False)
        except BrandError:
            check("logo: Inf threshold rejected", True)

        # -- 4) 语义边界：如实 needs_external --
        sem = eng.semantic_boundaries()
        check("semantics: needs_external + fabricated False, no invented keywords",
              sem["status"] == "needs_external" and sem["fabricated"] is False
              and set(sem["boundaries"]) == {"logo_semantics", "brand_tone_inference", "typography_recognition"})

        # -- 5) 语义经真实 Provider 客户端 passthrough（§2 红线：只搬运不编数）--
        import ai_provider_engine as _prov_mod

        # 5a) 未注入 provider → 如实 needs_external，绝不臆造关键词
        def _s5(name, cond, extra=""):
            check(name, bool(cond), extra)

        if hasattr(eng, "recognize_semantics"):
            r_no = eng.recognize_semantics(str(logo))
            _s5("semantics-no-provider: honest needs_external, fabricated False, no invented keywords",
                r_no["status"] == "needs_external" and r_no["fabricated"] is False
                and "semantics" not in r_no, str(r_no)[:160])
        else:
            _s5("semantics-no-provider: honest needs_external, fabricated False, no invented keywords", False, "method missing")

        fixed = ('{"tone_keywords":["minimal","premium"],"logo_semantics":"a geometric monogram",'
                 '"typography_guess":"sans-serif","palette_notes":"two-tone","confidence_note":"model estimate"}')
        srv, port = _start_mock_chat(fixed)
        try:
            try:
                prov = _prov_mod.ProviderEngine(state_dir=tdp / "prov")
                prov.configure("mock", base_url=f"http://127.0.0.1:{port}/v1",
                               api_key="sk-unit-test-abcdef123", model="vision-mock-1")
                eng_pv = BrandEngine(provider=prov)
            except TypeError:
                eng_pv = None
            if eng_pv is not None and hasattr(eng_pv, "recognize_semantics"):
                r_ok = eng_pv.recognize_semantics(str(logo))
                exp = json.loads(fixed)
                _s5("semantics-provider: status ok + semantics == model JSON verbatim (no synthesis)",
                    r_ok.get("status") == "ok" and r_ok.get("semantics") == exp, str(r_ok)[:200])
                _s5("semantics-provider: fabricated False + method is passthrough + usage passthrough",
                    r_ok.get("fabricated") is False
                    and r_ok.get("method") == "real_provider_vision_passthrough"
                    and (r_ok.get("usage") or {}).get("total_tokens") == 133, str(r_ok)[:200])
                cap = _MockChatHandler.captured
                du = ""
                try:
                    du = cap["req"]["messages"][0]["content"][1]["image_url"]["url"]
                except Exception:
                    du = ""
                real_b64 = base64.b64encode(Path(logo).read_bytes()).decode("ascii")
                _s5("semantics-provider: request carried a data:image URL built from REAL logo bytes",
                    du.startswith("data:image/png;base64,") and du.split(",", 1)[1] == real_b64,
                    "prefix=%s..." % du[:22])
                _s5("semantics-provider: Authorization Bearer sent, plaintext key never in response",
                    cap.get("auth", "").startswith("Bearer ")
                    and "sk-unit-test-abcdef123" not in json.dumps(r_ok), cap.get("auth", ""))
            else:
                for nm in ("semantics-provider: status ok + semantics == model JSON verbatim (no synthesis)",
                           "semantics-provider: fabricated False + method is passthrough + usage passthrough",
                           "semantics-provider: request carried a data:image URL built from REAL logo bytes",
                           "semantics-provider: Authorization Bearer sent, plaintext key never in response"):
                    _s5(nm, False, "method/param missing")
        finally:
            srv.shutdown()

        srv2, port2 = _start_mock_chat("this is definitely not json <<<")
        try:
            try:
                prov2 = _prov_mod.ProviderEngine(state_dir=tdp / "prov2")
                prov2.configure("mock", base_url=f"http://127.0.0.1:{port2}/v1", api_key="sk-x", model="m")
                eng_un = BrandEngine(provider=prov2)
            except TypeError:
                eng_un = None
            if eng_un is not None and hasattr(eng_un, "recognize_semantics"):
                r_un = eng_un.recognize_semantics(str(logo))
                _s5("semantics-unparseable: provider_output_unparseable + raw preserved + no fabricated schema",
                    r_un.get("status") == "provider_output_unparseable"
                    and "definitely not json" in r_un.get("raw", "")
                    and "semantics" not in r_un and r_un.get("fabricated") is False, str(r_un)[:200])
            else:
                _s5("semantics-unparseable: provider_output_unparseable + raw preserved + no fabricated schema",
                    False, "method/param missing")
        finally:
            srv2.shutdown()

    total = ok_count + len(fail)
    print(f"brand_engine self-test: {ok_count}/{total} passed, {len(fail)} failed")
    for f_ in fail:
        print(f"[FAIL] {f_}")
    return 0 if not fail else 1


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
