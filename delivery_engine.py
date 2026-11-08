# -*- coding: utf-8 -*-
"""
delivery_engine.py — 造境 AI 设计智能体 第 30 引擎
============================================================
一键交付包（目录树 + SHA256 校验清单 + README + ZIP）磁盘工作的单一真相源。

Phase 81（轨道 A 服务侧最后一刀）之前，_build_delivery_package 内联在 agent_service 的
/app/v1/delivery/package 端点里，故在 Registry 2.0 中恒为 evidence_kind=
no_golden_cases_yet——磁盘工作真实可观测，却因无独立引擎模块而无法被评测台黄金域核验。
本引擎把磁盘工作逐字下沉为可导入类，agent_service 的端点改为**只做**路径校验（依赖
security_engine 的 validate_file_path）与事件广播（依赖 event_engine），把逐文件复制、
SHA256 计算、manifest 落盘、README 生成、ZIP 打包全部委托本引擎（单一真相源，无平行
副本）；评测台再以一枚黄金用例真实驱动本引擎实例（私有沙箱、期望值手算），从而 verified
由磁盘最新报告如实派生。

诚实边界：本引擎只交付「已判定为普通文件的那批路径的归档机制」——把"文件是不是普通
文件 / 是不是被安全策略拦截"这类判断留在 agent_service 端点内的 classify_input_path
（其依赖 validate_file_path 属安全面），本引擎绝不越权判定路径合法性。收录侧对
数据根之外的文件**如实标注、不拦**（Phase 55 起 included_files[].inside_data_root 与
outside_root_count 逐条落账），拒收侧按三种责任形制（not_found / blocked_by_policy /
not_a_file）分账，绝不再合并成 missing_or_blocked 一刀切。

设计纪律（与既有引擎同源）：
  - 纯标准库（os/shutil/json/hashlib/zipfile/datetime/pathlib），零第三方依赖，零网络。
  - 每个实例绑定一个 data_root（服务实例=DATA_ROOT；评测实例=私有沙箱），实例间零串扰。
  - manifest.json 原子落盘（临时文件 + os.replace）；坏输入不半写。
  - ZIP 由 root.rglob 逐文件写入 namelist，读侧可用 namelist 长度与 manifest.included
    第三方对账——引擎自报 included==len(namelist) 属可证伪判据。
"""

import hashlib
import json
import logging
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("zaojing.delivery")


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class DeliveryEngine:
    """交付包的磁盘工作。构造参数 data_root 决定归档物理落点，
    每包独占子目录 deliveries/<pkg_id>/ 与同名 ZIP。"""

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)

    def package_dir(self, pkg_id: str) -> Path:
        return self.data_root / "deliveries" / pkg_id

    def zip_path(self, pkg_id: str) -> Path:
        return self.data_root / "deliveries" / f"{pkg_id}.zip"

    def inside_data_root(self, p: Path) -> bool:
        """源文件是否落在本引擎绑定的 data_root 之内（同 agent_service /health.data_root 口径）。"""
        try:
            return p.resolve().is_relative_to(self.data_root.resolve())
        except Exception:
            return False

    def build(self, pkg_id: str, project: str, notes: str,
              categories: List[str],
              plan: Dict[str, List[Path]],
              skipped: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Path]:
        """执行完整磁盘工作并返回 (manifest, zip_path)。

        plan 里的每一条都已由调用方（agent_service 端点）经 classify_input_path 确认
        是**普通文件**，故此处不再对目录或空串执行 copy2（Phase 55 修前正是那一步抛
        PermissionError，把整请求崩成 500）。
        """
        root = self.package_dir(pkg_id)
        root.mkdir(parents=True, exist_ok=True)
        manifest: Dict[str, Any] = {
            "package_id": pkg_id, "project": project,
            "created_at": datetime.now().isoformat(),
            "categories": {}, "included": 0, "skipped": skipped,
            "included_files": [], "outside_root_count": 0,
            "skipped_by_reason": {},
        }
        for category in categories:
            cat_dir = root / category
            cat_dir.mkdir(parents=True, exist_ok=True)
            entries: List[Dict[str, Any]] = []
            for src_path in plan.get(category, []):
                dst = cat_dir / src_path.name
                shutil.copy2(str(src_path), str(dst))
                digest = hashlib.sha256(dst.read_bytes()).hexdigest()
                entry = {"name": src_path.name, "category": category,
                         "sha256": digest, "size_bytes": dst.stat().st_size,
                         "source": str(src_path),
                         "inside_data_root": self.inside_data_root(src_path)}
                entries.append(entry)
                manifest["included_files"].append(dict(entry))
            manifest["categories"][category] = entries
            manifest["included"] += len(entries)

        manifest["outside_root_count"] = sum(1 for e in manifest["included_files"]
                                             if not e["inside_data_root"])
        for s in skipped:
            _r = str(s.get("reason") or "unknown")
            manifest["skipped_by_reason"][_r] = manifest["skipped_by_reason"].get(_r, 0) + 1

        _outside = [e for e in manifest["included_files"] if not e["inside_data_root"]]
        readme = root / "README.txt"
        readme.write_text(
            f"造境 AI 设计智能体 · 交付包\n项目: {project}\n包ID: {pkg_id}\n"
            f"生成时间: {manifest['created_at']}\n收录文件数: {manifest['included']}\n"
            + ("\n备注: " + notes if notes else "")
            + ("\n数据根之外的收录文件（如实标注，不拦）: "
               + ", ".join(f"[{e['category']}] {e['name']}" for e in _outside)
               if _outside else "")
            + f"\n拒收台账: {json.dumps(manifest['skipped_by_reason'], ensure_ascii=False)}\n"
            + "\n\n校验清单(SHA256):\n"
            + "\n".join(f"  [{c}] {e['name']}  {e['sha256']}"
                        for c, es in manifest["categories"].items() for e in es),
            encoding="utf-8",
        )
        _write_json_atomic(root / "manifest.json", manifest)

        zp = self.zip_path(pkg_id)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in root.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(root))
        return manifest, zp

    def tally_skipped(self, manifest: Dict[str, Any]) -> Dict[str, int]:
        """按三种责任形制各自计数，没发生的形制以 0 出现——缺席与为 0 是两回事。"""
        _tally = {"skipped_not_found": 0, "skipped_blocked_by_policy": 0,
                  "skipped_not_a_file": 0}
        for s in manifest.get("skipped", []):
            _key = "skipped_" + str(s.get("reason") or "unknown")
            _tally[_key] = _tally.get(_key, 0) + 1
        return _tally


# ---------------------------------------------------------------------------
# 自测（Phase 81：assert-gated，__main__ 走真断言，非恒真 PASS）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="zj_delivery_selftest_"))
    try:
        e = DeliveryEngine(d)
        # 私有工作沙箱：3 枚普通文件（Source/Preview/Report 三类各 1）+ 1 枚拒收
        # （not_a_file，因 skipped 由调用方传入，本引擎不判路径合法性）
        src_a = d / "src_a.txt"; src_a.write_bytes(b"alpha-bytes-A")
        src_b = d / "src_b.bin"; src_b.write_bytes(bytes(range(64)))
        outside = Path(tempfile.gettempdir()) / "zj_delivery_selftest_outside.bin"
        outside.write_bytes(b"OUTSIDE-DATA-ROOT")
        plan = {
            "Source": [src_a],
            "Preview": [src_b],
            "Report": [outside],
        }
        skipped = [{"category": "Print", "path": str(d / "gone.bin"),
                    "reason": "not_found", "detail": "no such file"},
                   {"category": "Print", "path": str(d / "sub"),
                    "reason": "not_a_file", "detail": "directory"}]
        m, zp = e.build(pkg_id="deliv_selftest", project="selftest", notes="n1",
                        categories=list(plan.keys()), plan=plan, skipped=skipped)
        # 1) 逐文件落账：included==3，sha256 由磁盘实物重算 == 引擎自报
        assert m["included"] == 3, m["included"]
        for es in m["categories"].values():
            for entry in es:
                disk_bytes = (e.package_dir("deliv_selftest") / entry["category"] / entry["name"]).read_bytes()
                assert hashlib.sha256(disk_bytes).hexdigest() == entry["sha256"], entry
        # 2) inside_data_root 逐条派生：src_a/src_b 在 d 内，outside 不在 → outside_root_count==1
        assert m["outside_root_count"] == 1, m["outside_root_count"]
        flags = {(x["category"], x["name"]): x["inside_data_root"] for x in m["included_files"]}
        assert flags[("Source", "src_a.txt")] is True, flags
        assert flags[("Preview", "src_b.bin")] is True, flags
        assert flags[("Report", outside.name)] is False, flags
        # 3) 拒收台账：两种形制各 1 条，未知形制不合并；缺席的形制以 0 出现在 tally
        assert m["skipped_by_reason"] == {"not_found": 1, "not_a_file": 1}, m["skipped_by_reason"]
        t = e.tally_skipped(m)
        assert t == {"skipped_not_found": 1, "skipped_blocked_by_policy": 0,
                     "skipped_not_a_file": 1}, t
        # 4) ZIP namelist 与 manifest 第三方对账（换桩即红：mock manifest 会不一致）
        with zipfile.ZipFile(zp) as zf:
            names = set(zf.namelist())
            # 3 收录 + README.txt + manifest.json == 5；拒收文件绝不入 ZIP
            assert len(names) == 5, names
            assert any(n.endswith("README.txt") for n in names), names
            assert any(n.endswith("manifest.json") for n in names), names
            # 拒收 gone.bin 与 sub 目录绝不能出现
            assert not any("gone.bin" in n for n in names), names
            # 收录三文件按 <category>/<name> 结构在 ZIP 里
            for cat, ename in [("Source", "src_a.txt"), ("Preview", "src_b.bin"),
                               ("Report", outside.name)]:
                candidate = f"{cat}/{ename}"
                assert candidate in names, (candidate, names)
        # 5) manifest.json 原子落盘可复读，且与内存副本全等（非半写、非伪造）
        m_reload = json.loads((e.package_dir("deliv_selftest") / "manifest.json").read_text(encoding="utf-8"))
        assert m_reload["included"] == m["included"], m_reload
        assert m_reload["included_files"] == m["included_files"], m_reload
        assert m_reload["skipped_by_reason"] == m["skipped_by_reason"], m_reload
        # 6) 空 plan（合法请求：所有路径皆被拒收）：included=0，tally 全 0，仍出 ZIP+manifest
        m2, zp2 = e.build(pkg_id="deliv_empty", project="empty", notes="",
                          categories=[], plan={}, skipped=[
                              {"category": "Print", "path": "/x", "reason": "blocked_by_policy",
                               "detail": "denied"}])
        assert m2["included"] == 0, m2
        assert m2["skipped_by_reason"] == {"blocked_by_policy": 1}, m2
        t2 = e.tally_skipped(m2)
        assert t2 == {"skipped_not_found": 0, "skipped_blocked_by_policy": 1,
                      "skipped_not_a_file": 0}, t2
        with zipfile.ZipFile(zp2) as zf:
            assert set(zf.namelist()) == {"README.txt", "manifest.json"}, zf.namelist()

        print("[PASS] delivery_engine self-test: build/sha/inside-root/skipped-tally/ZIP-namelist/atomic-reload/empty-plan")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        try:
            outside.unlink(missing_ok=True)
        except Exception:
            pass
