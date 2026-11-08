"""
造境 AI 设计智能体 · 客户改稿引擎（Customer Revision Engine）
===========================================================
任务书 §48 + §114（客户改稿语义解析）。流水线：
    自然语言 → 实体抽取 → 操作映射 → 目标定位 → 冲突检查 → ChangeRequest 列表 → 批量审核 → 应用

本引擎只做**可确定性复算**的部分，且对拿不准的地方诚实：
  - 操作映射：识别有限的显式指令词（大一点/缩小/换深红/字号减小/提亮/删掉…）→ 结构化 op。
  - 目标定位：在给定命名对象模型里按名称/角色/别名匹配。命中唯一→高置信；
    命中多个→返回 needs_clarification（§114.3 多轮澄清），**不猜**；命中零个→unresolved。
  - 冲突检查：被标记 protected 的对象（如"只允许改 Logo，不得动其他"）产生的改动→conflict 拒绝。
  - 未识别的自然语言片段一律归入 unparsed，绝不编造一条 change 冒充理解了语义。
  - 依赖视觉相似度/自由语义的"AI 定位"属于 needs_model，本引擎不谎称具备。

apply() 会把批准的 change 真实施加到对象列表上（缩放/改字色/改字号/调亮度/删除），
亮度、颜色均由真实色彩算法换算得出，并回传前后 diff。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("revision")

# 有限颜色词表（中英文）→ 十六进制。真实映射，非模型推断。
COLOR_WORDS: Dict[str, str] = {
    "红": "#D32F2F", "深红": "#8B0000", "暗红": "#8B0000", "大红": "#E53935", "绯红": "#DC143C",
    "蓝": "#1E88E5", "深蓝": "#0D47A1", "天蓝": "#03A9F4", "青": "#00838F",
    "绿": "#2E7D32", "深绿": "#1B5E20", "抹茶绿": "#7CB342", "墨绿": "#004D40",
    "黄": "#F9A825", "金": "#B8860B", "金色": "#B8860B", "橙": "#EF6C00",
    "黑": "#000000", "白": "#FFFFFF", "灰": "#9E9E9E", "紫": "#6A1B9A", "粉": "#EC407A",
    "red": "#D32F2F", "blue": "#1E88E5", "green": "#2E7D32", "black": "#000000", "white": "#FFFFFF",
    "gold": "#B8860B",
}

# 指令词 → 操作族。每条 (正则, op, params, 置信)
_OP_PATTERNS: List[Tuple[str, str, Dict[str, Any], float]] = [
    (r"(大一点|大一些|放大|大些|再大|bigger|larger|scale up)", "resize", {"scale": 1.2}, 0.9),
    (r"(小一点|小一些|缩小|小些|再小|变小|smaller|shrink|scale down)", "resize", {"scale": 0.8}, 0.9),
    (r"(字号.{0,4}(减小|调小|小|降)|字体.{0,4}(小|减)|decrease font|smaller text)", "font_size", {"delta": -4}, 0.7),
    (r"(字号.{0,4}(增大|调大|大|升)|字体.{0,4}(大|加)|更醒目|increase font|bigger text)", "font_size", {"delta": 4}, 0.7),
    (r"(提亮|亮一点|亮一些|更亮|加亮|brighter|lighten)", "brightness", {"delta": 0.15}, 0.8),
    (r"(变暗|暗一点|更暗|压暗|darker)", "brightness", {"delta": -0.15}, 0.8),
    (r"(删掉|删除|去掉|移除|去除|remove|delete)", "delete", {}, 0.9),
    (r"(旋转|转一下|rotate)", "rotate", {"deg": 90}, 0.6),
]

_COLOR_CHANGE_RE = re.compile(r"(换|改成|改为|变成|调成|设为)\s*([^\s，,。]+?)(色)?\b")


def _parse_color(text: str) -> Optional[str]:
    for w in sorted(COLOR_WORDS, key=len, reverse=True):
        if w in text:
            return COLOR_WORDS[w]
    m = re.search(r"#[0-9a-fA-F]{6}", text)
    return m.group(0).upper() if m else None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _adjust_brightness(hexcolor: str, delta: float) -> str:
    s = hexcolor.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    r, g, b = (int(s[i:i+2], 16) for i in (0, 2, 4))
    if delta >= 0:
        r, g, b = r + (255 - r) * delta, g + (255 - g) * delta, b + (255 - b) * delta
    else:
        k = 1 + delta
        r, g, b = r * k, g * k, b * k
    return "#{:02X}{:02X}{:02X}".format(*(int(_clamp(c, 0, 255)) for c in (r, g, b)))


@dataclass
class ChangeRequest:
    id: str
    target: Optional[str]
    op: str
    params: Dict[str, Any]
    confidence: float
    risk: str
    status: str            # proposed / needs_clarification / conflict / unresolved
    reason: str
    candidates: List[str] = field(default_factory=list)


class RevisionEngine:
    def __init__(self, default_protected: Optional[List[str]] = None):
        self.default_protected = default_protected or []

    def _index_objects(self, objects: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        idx = {}
        for i, o in enumerate(objects):
            oid = str(o.get("id", f"obj_{i}"))
            o["_id"] = oid
            idx[oid] = o
        return idx

    def _find_targets(self, phrase: str, objects: List[Dict[str, Any]]) -> List[str]:
        """在命名对象里按 name/role/label/aliases 做真实关键词匹配。"""
        hits = []
        pl = phrase.lower()
        for o in objects:
            names = [str(o.get("name", "")), str(o.get("role", "")), str(o.get("label", "")),
                     str(o.get("type", ""))] + [str(a) for a in o.get("aliases", [])]
            for n in names:
                n = n.strip()
                if n and n.lower() in pl:
                    hits.append(o["_id"])
                    break
        return list(dict.fromkeys(hits))

    def parse(self, customer_text: str, objects: List[Dict[str, Any]],
              protected: Optional[List[str]] = None) -> Dict[str, Any]:
        idx = self._index_objects(objects)
        protected_set = set(protected or self.default_protected)
        # 也尊重对象自带 protected 标志
        for oid, o in idx.items():
            if o.get("protected"):
                protected_set.add(oid)

        # 切分为子句
        clauses = [c.strip() for c in re.split(r"[，,。\n；;]", customer_text or "") if c.strip()]
        changes: List[ChangeRequest] = []
        unparsed: List[str] = []
        n = 0

        for clause in clauses:
            matched_any = False
            # 颜色替换
            cc = _COLOR_CHANGE_RE.search(clause)
            newcolor = None
            if cc:
                newcolor = _parse_color(cc.group(2)) or _parse_color(clause)
            targets = self._find_targets(clause, objects)

            ops = []
            for rx, op, params, conf in _OP_PATTERNS:
                if re.search(rx, clause, re.IGNORECASE):
                    ops.append((op, dict(params), conf))
            if newcolor:
                ops.append(("color_change", {"color": newcolor}, 0.85))

            if not ops:
                # 只提到对象但没有任何可识别动作 → 记录为待澄清/未解析，绝不编造
                if targets:
                    unparsed.append({"clause": clause, "resolved_targets": targets,
                                     "note": "识别到对象但未识别到可执行操作词"})
                else:
                    unparsed.append({"clause": clause, "note": "未识别到已知对象或操作（需语义模型或人工澄清）"})
                continue

            for op, params, conf in ops:
                matched_any = True
                n += 1
                cid = f"chg_{n:02d}"
                if len(targets) == 0:
                    changes.append(ChangeRequest(cid, None, op, params, round(conf * 0.4, 2), "unknown",
                                                 "unresolved", "未定位到目标对象，需澄清或语义模型"))
                elif len(targets) == 1:
                    t = targets[0]
                    if t in protected_set:
                        changes.append(ChangeRequest(cid, t, op, params, conf, "high",
                                                     "conflict", f"目标 {t} 为受保护对象，改动被拒绝（§114 冲突检查）"))
                    else:
                        risk = "L1" if op in ("resize", "font_size", "brightness", "color_change") else "L3"
                        changes.append(ChangeRequest(cid, t, op, params, conf, risk,
                                                     "proposed", "定位到唯一对象并映射到显式操作"))
                else:
                    changes.append(ChangeRequest(cid, None, op, params, round(conf * 0.6, 2), "medium",
                                                 "needs_clarification", "匹配到多个候选对象，请指定其一",
                                                 candidates=targets))

        by_status: Dict[str, int] = {}
        for c in changes:
            by_status[c.status] = by_status.get(c.status, 0) + 1
        return {
            "input": customer_text,
            "clause_count": len(clauses),
            "summary": {"changes": len(changes), "by_status": by_status, "unparsed_clauses": len(unparsed)},
            "changes": [asdict(c) for c in changes],
            "unparsed": unparsed,
        }

    def apply(self, objects: List[Dict[str, Any]], changes: List[Dict[str, Any]],
              only_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """把批准的 change 真实施加到对象副本上，返回新对象列表 + 逐条结果 + 前后 diff。"""
        idx = self._index_objects([dict(o) for o in objects])
        results = []
        applied = 0
        for ch in changes:
            if only_ids and ch.get("id") not in only_ids:
                continue
            cid, op, params, tgt, status = ch.get("id"), ch.get("op"), ch.get("params", {}), ch.get("target"), ch.get("status")
            if status != "proposed" or not tgt or tgt not in idx:
                results.append({"id": cid, "op": op, "applied": False, "reason": f"状态为 {status}，未批准执行"})
                continue
            o = idx[tgt]
            before = {k: o.get(k) for k in ("width", "height", "font_size", "color", "rotation")}
            try:
                if op == "resize":
                    s = float(params.get("scale", 1.0))
                    o["width"] = round(o.get("width", 0) * s, 2)
                    o["height"] = round(o.get("height", 0) * s, 2)
                elif op == "font_size":
                    o["font_size"] = round(max(1, o.get("font_size", 12) + float(params.get("delta", 0))), 2)
                elif op == "color_change":
                    o["color"] = params["color"]
                elif op == "brightness":
                    base = o.get("color", "#808080")
                    o["color"] = _adjust_brightness(base, float(params.get("delta", 0)))
                elif op == "rotate":
                    o["rotation"] = round((o.get("rotation", 0) + float(params.get("deg", 0))) % 360, 2)
                elif op == "delete":
                    del idx[tgt]
                    results.append({"id": cid, "op": op, "target": tgt, "applied": True, "before": before})
                    applied += 1
                    continue
                after = {k: o.get(k) for k in ("width", "height", "font_size", "color", "rotation")}
                results.append({"id": cid, "op": op, "target": tgt, "applied": True, "before": before, "after": after})
                applied += 1
            except Exception as e:
                results.append({"id": cid, "op": op, "target": tgt, "applied": False, "reason": f"应用失败: {e}"})

        new_objects = [o for o in idx.values()]
        return {
            "applied": applied,
            "objects": new_objects,
            "results": results,
            "diff": {"before_count": len(objects), "after_count": len(new_objects)},
        }


if __name__ == "__main__":
    eng = RevisionEngine()
    objs = [
        {"id": "obj_logo", "name": "logo", "type": "image", "width": 200, "height": 80, "color": "#123456"},
        {"id": "obj_title", "name": "主标题", "type": "text", "font_size": 36, "color": "#111111"},
        {"id": "obj_subtitle", "name": "副标题", "type": "text", "font_size": 20},
        {"id": "obj_bg", "name": "背景", "type": "shape", "width": 1000, "height": 1000, "protected": True},
    ]
    text = "logo 再大一点，主标题换成深红色，背景删掉，主标题和副标题都缩小一点"
    parsed = eng.parse(text, objs)
    print("[OK] summary:", parsed["summary"])
    kinds = {(c["op"], c["status"]) for c in parsed["changes"]}
    assert ("resize", "proposed") in kinds, "logo 放大应为 proposed"
    assert ("color_change", "proposed") in kinds, "主标题换色命中唯一对象应为 proposed"
    assert any(s == "conflict" for _, s in kinds), "改背景应触发保护冲突"
    assert ("resize", "needs_clarification") in kinds, "多候选(主标题+副标题)缩小应触发澄清"
    approved = [c for c in parsed["changes"] if c["status"] == "proposed"]
    applied = eng.apply(objs, parsed["changes"], only_ids=[c["id"] for c in approved])
    logo = next(o for o in applied["objects"] if o["_id"] == "obj_logo")
    title = next(o for o in applied["objects"] if o["_id"] == "obj_title")
    assert logo["width"] == 240.0, f"logo 应放大到 240，实际 {logo['width']}"
    assert title["color"] == "#8B0000", f"主标题应变深红，实际 {title['color']}"
    print("[OK] applied:", applied["applied"], "logo w=", logo["width"], "title color=", title["color"])
    print("[PASS] revision self-test")
