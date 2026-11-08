# -*- coding: utf-8 -*-
"""设计版本对比引擎（第 23 引擎，Phase 38 自 agent_service 内联实现逐字下沉）。

模块即引擎：compare_objects 为纯函数——对象列表进出，零 I/O、零实例状态、
零时间/随机字段，真值面完全确定可复现。下沉时算法逐字保真（字段→类别
映射、x/y 双计、结构增删按 type 归类、未知字段兜底 visual、未知 type 兜底
layer），agent_service 端点已改薄包装，既有 L2/L4 契约断言作回归安全网。

§50：Version A vs Version B → Geometry / Text / Color / Asset / Layer / Visual 六类结构化 diff。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# 字段→类别映射（按顺序首个命中归入该类别；未命中的改动归入 Visual 作为"其它视觉属性"）。
COMPARE_CATEGORY_FIELDS = {
    "geometry": ("x", "y", "width", "height", "rotation", "transform", "scale",
                 "size", "path", "points", "position", "anchor", "corner_radius", "radius"),
    "text": ("text", "content", "string", "value", "font_size", "font_family", "font",
             "line_height", "leading", "letter_spacing", "tracking", "alignment", "align",
             "font_weight", "style_attr"),
    "color": ("color", "fill", "stroke", "background", "gradient", "text_color",
              "fill_color", "stroke_color", "hex"),
    "asset": ("src", "asset_id", "image", "asset", "link", "file", "href", "resource_id",
              "pixel_width", "pixel_height", "embedded", "asset_name"),
    "layer": ("z", "z_index", "layer", "visible", "locked", "group", "parent",
              "blend_target", "layer_name"),
    "visual": ("opacity", "blend_mode", "shadow", "blur", "effect", "mask", "filter",
               "glow", "transparency"),
}
# object.type → 结构增删所归属的类别
TYPE_TO_CATEGORY = {
    "text": "text", "image": "asset", "asset": "asset", "shape": "geometry",
    "background": "color", "layer": "layer",
}


def categorize_field(fld: str) -> str:
    f = (fld or "").lower()
    for cat, fields in COMPARE_CATEGORY_FIELDS.items():
        if f in fields:
            return cat
    return "visual"      # 未显式列出的可视/其它属性兜底归入 Visual


def compare_objects(before: List[Dict[str, Any]], after: List[Dict[str, Any]],
                    compare_keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """对 before/after 对象列表做真实差异分析：新增/删除/改动/移动，
    并按 §50 归类为六类结构化 diff。逐字迁移自 agent_service 内联实现。"""

    def index(items):
        out = {}
        for i, it in enumerate(items):
            out[str(it.get("id", it.get("name", i)))] = it
        return out

    b, a = index(before), index(after)
    added = [{"key": k, "after": a[k]} for k in a if k not in b]
    removed = [{"key": k, "before": b[k]} for k in b if k not in a]

    # 字段全集：显式 compare_keys 优先；否则取两侧对象出现过的所有字段（更完整的真实 diff）
    if compare_keys:
        field_universe = [f for f in compare_keys if f not in ("id", "name")]
    else:
        field_universe = []
        seen = set(["id", "name"])
        for k in set(b) & set(a):
            for fld in list(b[k].keys()) + list(a[k].keys()):
                if fld not in seen:
                    seen.add(fld)
                    field_universe.append(fld)

    changed, moved = [], []
    categories: Dict[str, List[Dict[str, Any]]] = {c: [] for c in COMPARE_CATEGORY_FIELDS}

    for k in sorted(set(b) & set(a)):
        diffs = {fld: {"before": b[k].get(fld), "after": a[k].get(fld)}
                 for fld in field_universe if b[k].get(fld) != a[k].get(fld)}
        pos_changed = {f: diffs[f] for f in ("x", "y") if f in diffs}
        other = {f: v for f, v in diffs.items() if f not in ("x", "y")}
        if pos_changed:
            moved.append({"key": k, "position": pos_changed})
        if other:
            changed.append({"key": k, "properties": other})
        # 逐字段归类（位置 x/y 同时计入 geometry）
        for fld, dv in diffs.items():
            cat = categorize_field(fld)
            categories[cat].append({"key": k, "field": fld,
                                    "before": dv["before"], "after": dv["after"]})

    # 结构增删按其对象 type 归入相应类别（本质是图层/对象结构变化）
    for entry in added:
        otype = str((entry["after"] or {}).get("type", "")).lower()
        cat = TYPE_TO_CATEGORY.get(otype, "layer")
        categories[cat].append({"key": entry["key"], "field": "*", "op": "added",
                                "before": None, "after": entry["after"]})
    for entry in removed:
        otype = str((entry["before"] or {}).get("type", "")).lower()
        cat = TYPE_TO_CATEGORY.get(otype, "layer")
        categories[cat].append({"key": entry["key"], "field": "*", "op": "removed",
                                "before": entry["before"], "after": None})

    by_category = {c: len(items) for c, items in categories.items()}
    return {
        "summary": {"added": len(added), "removed": len(removed),
                    "changed": len(changed), "moved": len(moved),
                    "by_category": by_category},
        "added": added, "removed": removed, "changed": changed, "moved": moved,
        "categories": categories,
        "identical": not (added or removed or changed or moved),
    }


if __name__ == "__main__":
    # 自测全部为逐数真值断言：常量桩/归类倒置无法通过（§2 红线：判分可证伪）
    r0 = compare_objects([{"id": "logo", "x": 0}], [{"id": "logo", "x": 0}])
    assert r0["identical"] is True and r0["summary"]["by_category"] == {
        "geometry": 0, "text": 0, "color": 0, "asset": 0, "layer": 0, "visual": 0}, r0
    print("  [OK] 空 diff：identical=True 且六类计数全 0")

    r1 = compare_objects(
        [{"id": "t1", "x": 10, "y": 20, "fill_color": "#FF0000", "text": "A", "wobble": 1}],
        [{"id": "t1", "x": 15, "y": 20, "fill_color": "#00FF00", "text": "A", "wobble": 2}])
    assert r1["summary"]["changed"] == 1 and r1["summary"]["moved"] == 1, r1["summary"]
    bc = r1["summary"]["by_category"]
    assert bc == {"geometry": 1, "text": 0, "color": 1, "asset": 0,
                  "layer": 0, "visual": 1}, bc
    assert r1["moved"] == [{"key": "t1", "position": {"x": {"before": 10, "after": 15}}}]
    assert r1["changed"][0]["properties"] == {
        "fill_color": {"before": "#FF0000", "after": "#00FF00"},
        "wobble": {"before": 1, "after": 2}}
    print("  [OK] 单对象混合改动：x 移动双计 geometry、fill_color→color、未知字段兜底 visual")

    r2 = compare_objects(
        [{"id": "a", "type": "shape", "z": 1}, {"id": "gone", "type": "mystery"}],
        [{"id": "a", "type": "shape", "z": 2},
         {"id": "newimg", "type": "image"}, {"id": "newx", "type": "whatever"}])
    assert r2["summary"]["added"] == 2 and r2["summary"]["removed"] == 1, r2["summary"]
    bc2 = r2["summary"]["by_category"]
    # layer = a 的 z 改动 1 + gone(未知 type) 移除兜底 1 + newx(未知 type) 新增兜底 1
    assert bc2 == {"geometry": 0, "text": 0, "color": 0, "asset": 1,
                   "layer": 3, "visual": 0}, bc2
    print("  [OK] 结构增删按 type 归类：image→asset、未知 type 兜底 layer（含 z 改动入 layer）")

    r3 = compare_objects(
        [{"id": "k", "x": 0, "font_size": 12, "opacity": 0.5}],
        [{"id": "k", "x": 9, "font_size": 30, "opacity": 0.1}],
        compare_keys=["x", "font_size"])
    assert r3["summary"]["by_category"]["visual"] == 0, r3["summary"]
    assert r3["summary"]["changed"] == 1 and r3["summary"]["moved"] == 1, r3["summary"]
    assert set(r3["changed"][0]["properties"]) == {"font_size"}
    print("  [OK] compare_keys 显式字段集：unlisted opacity 改动不参与 diff")

    assert categorize_field("FONT_SIZE") == "text" and categorize_field(None) == "visual"
    print("  [OK] 归类助手：大小写归一 + None 兜底 visual")

    print("\n" + "=" * 60)
    print("[PASS] 设计版本对比引擎自测全部通过（纯函数逐数真值，常量桩无法通过）")
    print("=" * 60)
