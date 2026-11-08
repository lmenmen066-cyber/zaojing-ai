# -*- coding: utf-8 -*-
"""真实像素级去背景引擎（第 24 引擎，Phase 38 自 agent_service 内联实现逐字下沉）。

edge-flood-fill：从图像四边全部像素出发 BFS 泛洪，仅清除"与四边采样均值色
距离在容差内、且与边缘连通"的区域，输出透明 PNG。整数运算、无随机、无时间
字段——同一输入图像 + 同一阈值 ⇒ 同一逐像素结果（真值面确定可复现）。

关键语义（区别于全局色键抠图）：主体内部被包围的背景色像素因不与边缘连通
而必然保留——这是本引擎"泛洪填充"而非"颜色替换"的可证伪证据。
agent_service 端点已改薄包装，既有 L2/L4 契约断言作回归安全网。
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Dict, Optional

ALGORITHM = "edge-flood-fill (PIL, real pixel processing)"


def remove_background(image_path, output_path, threshold: int = 32) -> Dict[str, Any]:
    """对 image_path 执行真实泛洪去背景并保存到 output_path（RGBA PNG）。

    阈值钳制 0-255（越界如实钳到边界值，不报错不伪造）。输入不存在时抛
    FileNotFoundError 并携中文定位文案——诚实报错，绝不返回假成功。
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"输入图像不存在: {path}")
    from PIL import Image

    out = Path(output_path) if output_path else path.parent / f"{path.stem}_no_bg.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    im = Image.open(path).convert("RGBA")
    px = im.load()
    w, h = im.size
    # 采样四边中点均值作为背景色
    samples = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1],
               px[w // 2, 0], px[w // 2, h - 1], px[0, h // 2], px[w - 1, h // 2]]
    bg = tuple(sum(s[c] for s in samples) // len(samples) for c in range(3))
    thr = max(0, min(255, int(threshold)))
    thr2 = thr * thr * 3
    seen = [[False] * h for _ in range(w)]
    q = deque()
    for x in range(w):
        q.append((x, 0)); q.append((x, h - 1))
    for y in range(h):
        q.append((0, y)); q.append((w - 1, y))
    removed = 0
    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[x][y]:
            continue
        seen[x][y] = True
        r_, g_, b_, a_ = px[x, y]
        d = (r_ - bg[0]) ** 2 + (g_ - bg[1]) ** 2 + (b_ - bg[2]) ** 2
        if d <= thr2:
            px[x, y] = (r_, g_, b_, 0)
            removed += 1
            q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    im.save(out, "PNG")
    return {
        "input": str(path),
        "output": str(out),
        "algorithm": ALGORITHM,
        "background_sample": list(bg),
        "threshold_used": thr,
        "width": w,
        "height": h,
        "pixels_cleared": removed,
        "background_cleared_ratio": removed / (w * h),
        "mode": im.mode,
    }


if __name__ == "__main__":
    import tempfile
    import shutil
    from PIL import Image

    root = Path(tempfile.mkdtemp(prefix="zj_bgselftest_"))
    try:
        # 夹具：100x100 纯背景 (200,30,30)，中央 40x40 方块 (20,20,220)，
        #        方块内再挖 8x8 背景色孔洞（与边缘不连通 → 泛洪必不达，可证伪核心）
        src = root / "subject.png"
        im = Image.new("RGB", (100, 100), (200, 30, 30))
        px = im.load()
        for y in range(30, 70):
            for x in range(30, 70):
                px[x, y] = (20, 20, 220)
        for y in range(40, 48):
            for x in range(40, 48):
                px[x, y] = (200, 30, 30)
        im.save(src, "PNG")

        r = remove_background(src, root / "out" / "no_bg.png", 32)
        # 逐数真值：bg 采样 (200,30,30)；清除 = 全背景 9600 + 方块 1600 - 孔洞 64 - …
        # 期望由本夹具几何直接可核：总 10000，方块 1600，孔洞 64（背景色但保留）
        assert r["background_sample"] == [200, 30, 30], r["background_sample"]
        assert r["pixels_cleared"] == 10000 - 1600, r["pixels_cleared"]
        assert abs(r["background_cleared_ratio"] - 0.84) < 1e-12, r["background_cleared_ratio"]
        out_im = Image.open(r["output"])
        assert out_im.mode == "RGBA"
        assert out_im.getpixel((0, 0))[3] == 0            # 边缘背景已清
        assert out_im.getpixel((31, 31))[3] == 255        # 方块保留
        assert out_im.getpixel((43, 43))[3] == 255        # 孔洞背景色但被包围 → 必保留（非全局色键）
        print("  [OK] 泛洪真值：逐数清除 8400px、ratio 0.84、包围孔洞背景色不清除（泛洪≠色键）")

        r0 = remove_background(src, root / "out0.png", 0)
        assert r0["pixels_cleared"] == r["pixels_cleared"], "阈值 0 已覆盖精确等色泛洪"
        assert r0["threshold_used"] == 0
        rhi = remove_background(src, root / "outhi.png", 999)
        assert rhi["threshold_used"] == 255, "越界阈值如实钳到 255，不报错不伪造"
        # 255 容差距离阈 = 3*255²（色空间最大方距），本夹具方块方距 68600 < 195075 →
        # 泛洪可达全幅：最大容差如实清空一切（含包围孔洞），算法边界的可证伪真值
        assert rhi["pixels_cleared"] == 10000, rhi["pixels_cleared"]
        print("  [OK] 阈值边界：0 精确保留主体 / 255 如实清空全幅（钳制不报错，容差语义钉死）")

        try:
            remove_background(root / "ghost.png", root / "never.png", 32)
            raise AssertionError("输入不存在必须抛 FileNotFoundError，却静默返回")
        except FileNotFoundError as e:
            assert "输入图像不存在" in str(e), str(e)
        assert not (root / "never.png").exists(), "失败路径绝不落盘输出（fail-no-pass-on）"
        print("  [OK] 幽灵输入：FileNotFoundError 中文文案 + 绝不落盘假产物")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 60)
    print("[PASS] 去背景引擎自测全部通过（真像素泛洪逐数钉死，常量桩/色键冒泛洪无法通过）")
    print("=" * 60)
