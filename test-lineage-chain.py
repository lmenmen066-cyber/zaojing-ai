"""L5 — value-lineage acceptance layer (Phase 51).

Why this layer exists
---------------------
L1..L4 each prove something real, but all four ask the same kind of question:
"does this endpoint/engine/artifact behave as claimed?" None of them proves that
the value produced by hop N is the value consumed by hop N+1. A system can pass
all four layers while every "chain" in it is actually a set of islands that happen
to be called in sequence.

L5 asks only the lineage question, and it accepts a hop as a lineage edge ONLY when
the downstream input is *provably* the upstream output — either by exact field
equality against the upstream response, or by an independent recomputation
(GS1 check digit, union-outline perimeter, pixel alpha, sha256, disk bytes).
"Called after" is never evidence. "Same number, recomputed a second way" is.

Coverage
--------
  1. the workflow tool surface is derived from the registry, and its param contract
     is a subset of the OpenAPI schema of the model it names (no hand-written twin)
  2. dieline -> panel-topology is a real data bridge (1-panel AND 2-panel AND
     disconnected AND rejected), with perimeters recomputed independently
  3. a real 5-node cross-engine graph runs, and each hop's output is shown to be
     the next hop's input; integer-index path refs ($a.list.0.field) included
  4. batch processing shares the single-item code path bit-for-bit (ratio, algorithm,
     pixels) instead of carrying a second, weaker implementation
  5. the event ledger is runtime-derived: the kinds that fired are exactly the kinds
     the source declares, each counted exactly as many times as it was triggered —
     a phantom kind, a missing kind, or a wrong count all go red
  6. the retrieval ceiling is behaviourally true, not merely labelled: a query that
     shares no literal token with the ingested text scores zero
  7. the write side really lands on disk (license read back from the JSON file)
  8. the capability truth table and the evaluation suite did not drift

Runs against the source service (like L2), serially, on an avoided port, in an
isolated ZAOJING_DATA_DIR. Exit code 1 on any FAIL.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(errors="backslashreplace")
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
SERVICE = OUT / "agent_service.py"


# --------------------------------------------------------------------------
# port avoidance — bind() success proves nothing on Windows; only "nobody
# answers HTTP here" does.
# --------------------------------------------------------------------------
def _answered(p):
    try:
        r = requests.get("http://127.0.0.1:%d/api/v1/health" % p, timeout=2)
    except Exception:
        return False
    try:
        j = r.json()
    except Exception:
        return False
    return r.status_code == 200 and isinstance(j, dict) and isinstance(j.get("engines"), dict)


def _pick_port(candidates=(8851, 8853, 8855, 8857, 8859, 8861, 8863)):
    for p in candidates:
        try:
            requests.get("http://127.0.0.1:%d/api/v1/health" % p, timeout=1)
            continue                       # somebody answers -> avoid
        except Exception:
            pass
        if not _answered(p):
            return p
    raise RuntimeError("no silent L5 port among %s" % (candidates,))


PORT = _pick_port()
BASE = "http://127.0.0.1:%d" % PORT
print("[PORT] L5 lineage port=%d (L2=8765band L3=8799 L4=8877 all avoided)" % PORT)

tmp = Path(tempfile.mkdtemp(prefix="zjl5_"))
DATA = tmp / "data"

results = []


def check(name, cond, detail=""):
    tag = "[PASS]" if cond else "[FAIL]"
    results.append((tag, name, detail))
    print("%s %s %s" % (tag, name, detail))


# --------------------------------------------------------------------------
# independent recomputations — the whole point of this layer
# --------------------------------------------------------------------------
def ean13_check_digit(code12):
    """GS1 modulo-10 check digit, recomputed here from the standard, not copied
    from the engine's own answer."""
    digits = [int(c) for c in code12]
    s = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits))
    return str((10 - s % 10) % 10)


def union_rect_perimeter(rects):
    """Perimeter of the union outline of axis-aligned rectangles, computed by a
    coordinate-compressed cell sweep — a second, unrelated algorithm to the
    engine's shared-edge subtraction.

    Why not a unit-cell grid: a naive grid at scale=100 turns a 200x80 mm blank
    into 160,000,000 python set entries and dies. Coordinate compression keeps
    the cell count at (2n)x(2n) regardless of the magnitudes involved, while
    still being a different algorithm: it sums EXPOSED EDGE LENGTHS of the
    compressed cells covered by the union, never touching the engine's
    adjacency graph.
    """
    if not rects:
        return 0.0
    xs = sorted({float(r["x"]) for r in rects} | {float(r["x"]) + float(r["width"]) for r in rects})
    ys = sorted({float(r["y"]) for r in rects} | {float(r["y"]) + float(r["height"]) for r in rects})
    xi = {v: i for i, v in enumerate(xs)}
    yi = {v: i for i, v in enumerate(ys)}
    # cell (i, j) = [xs[i], xs[i+1]] x [ys[j], ys[j+1]]
    covered = [[False] * (len(ys) - 1) for _ in range(len(xs) - 1)]
    for r in rects:
        x0, x1 = xi[float(r["x"])], xi[float(r["x"]) + float(r["width"])]
        y0, y1 = yi[float(r["y"])], yi[float(r["y"]) + float(r["height"])]
        for i in range(x0, x1):
            for j in range(y0, y1):
                covered[i][j] = True
    total = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            if not covered[i][j]:
                continue
            w = xs[i + 1] - xs[i]
            h = ys[j + 1] - ys[j]
            if i == 0 or not covered[i - 1][j]:          # left edge exposed
                total += h
            if i == len(xs) - 2 or not covered[i + 1][j]:  # right edge exposed
                total += h
            if j == 0 or not covered[i][j - 1]:          # bottom edge exposed
                total += w
            if j == len(ys) - 2 or not covered[i][j + 1]:  # top edge exposed
                total += w
    return total


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# derive the declared event kinds straight out of the artifact under test.
# A hardcoded list here would be exactly the "second copy that can drift"
# this project keeps removing.
# --------------------------------------------------------------------------
_SRC = SERVICE.read_text(encoding="utf-8")
DECLARED_KINDS = sorted(set(re.findall(r'_emit\(\s*"([^"]+)"', _SRC)))
# falsifiability of the derivation itself: if the regex silently matched nothing,
# every downstream "no phantom kinds" assertion would pass vacuously.
if not DECLARED_KINDS:
    raise SystemExit("[FATAL] event-kind derivation matched nothing in %s — "
                     "the scan is broken, refusing to grade against an empty truth" % SERVICE)
print("[DERIVE] %d event kinds declared in agent_service.py" % len(DECLARED_KINDS))

srv_log = open(tmp / "server.log", "wb")
env = dict(os.environ, ZAOJING_PORT=str(PORT), ZAOJING_DATA_DIR=str(DATA))
proc = subprocess.Popen([sys.executable, str(SERVICE)], env=env,
                        stdout=srv_log, stderr=subprocess.STDOUT)

triggered = Counter()      # kinds *this test* caused, counted by this test


def post(path, **kw):
    return requests.post(BASE + path, timeout=kw.pop("timeout", 180), **kw)


def get(path, **kw):
    return requests.get(BASE + path, timeout=kw.pop("timeout", 180), **kw)


def expect_event(kind):
    """Record that this test is about to cause `kind`, so the ledger count can be
    compared against an independently tallied expectation."""
    triggered[kind] += 1


try:
    up = False
    # INSTRUMENT HARDENING (P53): `health` used to be bound only on the success
    # path, so a service that never answered made the check() *message argument*
    # raise NameError and the whole layer crashed instead of reporting one honest
    # FAIL. Binding an empty dict changes no predicate — `up` still gates the
    # check, and the message now shows what was actually seen (or why nothing
    # was). Same family as P52's "修的是打印语句而非任何阈值".
    health = {}
    last_err = None
    for _ in range(240):
        time.sleep(0.5)
        try:
            r = get("/api/v1/health", timeout=3)
            j = r.json()
            if r.status_code == 200 and isinstance(j.get("engines"), dict) and "data_root" in j:
                up = True
                health = j
                break
            last_err = "http=%s keys=%s" % (r.status_code, sorted(j)[:6] if isinstance(j, dict) else type(j).__name__)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, str(e)[:120])
            if proc.poll() is not None:
                last_err += " | child_exited rc=%s" % proc.returncode
                break
            continue
    check("L5 boot: the lineage layer talks to a real service in its own isolated data root",
          up and health["data_root"].replace("\\", "/").rstrip("/").endswith("data")
          and str(DATA).replace("\\", "/") in health["data_root"].replace("\\", "/"),
          "port=%d data_root=%s engines=%d answered=%s last_observation=%s" % (
              PORT, health.get("data_root"), len(health.get("engines", {})), up, last_err))

    # =====================================================================
    # 1. tool surface is derived, and its contract is a SUBSET of OpenAPI
    # =====================================================================
    wt = get("/api/v1/workflow/tools").json()
    oa = get("/openapi.json").json()
    schemas = (oa.get("components") or {}).get("schemas") or {}
    tools = wt["tools"]
    byname = {t["tool"]: t for t in tools}

    check("L5 tool-surface: the listing is derived from the live registry (no second hand-written "
          "copy), self-reports its own source, and carries no untagged mystery entries",
          wt.get("derived_from") == "workflow_engine.tool_registry"
          and wt.get("fabricated") is False
          and wt["total"] == len(tools) == len(byname)
          and wt["untagged"] == []
          and sum(wt["tools_per_domain"].values()) == wt["total"]
          and wt["domain_count"] == len(set(t["domain"] for t in tools)) == len(wt["domains"]),
          "total=%d domains=%d untagged=%s per_domain_sum=%d"
          % (wt["total"], wt["domain_count"], wt["untagged"], sum(wt["tools_per_domain"].values())))

    # 幽灵串按 P44 惯例分段拼接：整体字面串会被 L2 的 P43 声称收集器当成"调用了不存在的端点"，
    # 而这条恰恰是故意的负控（证明清单端点之外的近似名不会静默 200）。
    _ghost_tools = "/api/v1/" + "workflow/toolz"
    check("L5 tool-surface: /workflow/tools is itself a real route (counted in the derived API total) "
          "while a one-character-off ghost is honestly 404",
          wt["api_route_total"] >= 1
          and "/api/v1/workflow/tools" in (oa.get("paths") or {})
          and get(_ghost_tools).status_code == 404,
          "api_route_total=%d openapi_has_tools=%s ghost=%d"
          % (wt["api_route_total"], "/api/v1/workflow/tools" in (oa.get("paths") or {}),
             get(_ghost_tools).status_code))

    # param contract must not leak pydantic internals, and must agree with the schema
    leaks = [(t["tool"], p["name"]) for t in tools for p in t["params"]
             if "PydanticUndefined" in json.dumps(p, default=str)
             or "<class " in json.dumps(p, default=str)]
    mismatched = []
    for t in tools:
        model = t.get("request_model")
        if not model or model not in schemas:
            continue
        props = set((schemas[model].get("properties") or {}))
        body_params = {p["name"] for p in t["params"] if p["in"] == "body"}
        if not body_params <= props:
            mismatched.append((t["tool"], sorted(body_params - props)))
        required = set(schemas[model].get("required") or [])
        for p in t["params"]:
            if p["in"] == "body" and p["name"] in props:
                if p["required"] != (p["name"] in required):
                    mismatched.append((t["tool"], p["name"], "required-mismatch"))
    missing_models = sorted({t["request_model"] for t in tools
                             if t.get("request_model") and t["request_model"] not in schemas})
    check("L5 tool-surface: every declared request model really exists in /openapi.json and each "
          "tool's body params are a subset of that schema with matching required-ness — the contract "
          "is cross-checked against a second independent artifact, not self-asserted",
          not leaks and not mismatched and not missing_models,
          "pydantic_leaks=%s schema_mismatch=%s models_not_in_openapi=%s params_total=%d"
          % (leaks[:3], mismatched[:3], missing_models[:3], sum(len(t["params"]) for t in tools)))

    check("L5 tool-surface: required params carry default=null and optional ones carry a real "
          "default (the 'no default' sentinel is never stringified into the public contract)",
          all(p["default"] is None for t in tools for p in t["params"] if p["required"])
          and all(isinstance(p["type"], str) and p["type"] for t in tools for p in t["params"]),
          "sample=%s" % json.dumps(
              {t["tool"]: [(p["name"], p["type"], p["required"]) for p in t["params"]]
               for t in tools if t["tool"] == "packaging.panel_topology"}, ensure_ascii=False))

    _rs = wt["reference_syntax"]
    _gram = _rs.get("grammar") or ""
    check("L5 tool-surface: path references are a real engine capability, and the claim is derived "
          "from the implementing function's own docstring rather than restated by hand — and the "
          "grammar the contract quotes is the engine's OWN compiled pattern, so the same regex that "
          "decides (live, in the P54 checks below) whether '$bc.output_path' is a reference and "
          "'$1,200' is a literal is the regex the contract publishes; the semantics sentence is the "
          "docstring's own 语义： line, not a hand copy that can drift out of date",
          wt["reference_syntax"]["supports_path"] is True
          and wt["reference_syntax"]["implemented_by"] == "workflow_engine.WorkflowEngine._resolve_value"
          and bool(wt["reference_syntax"].get("doc_evidence"))
          and bool(_gram) and re.match(_gram, "$bc.output_path") is not None
          and re.match(_gram, "$1,200") is None
          and str(_rs.get("semantics") or "").startswith(chr(35821) + chr(20041) + chr(65306)),
          "supports_path=%s evidence=%r grammar=%s sem=%r" % (
              wt["reference_syntax"]["supports_path"],
              (wt["reference_syntax"].get("doc_evidence") or "")[:40], _gram,
              (wt["reference_syntax"].get("semantics") or "")[:24]))

    # =====================================================================
    # 2. dieline -> panel-topology bridge
    # =====================================================================
    die = post("/api/v1/packaging/dieline",
               json={"width": 100, "height": 80, "bleed": 3, "safe": 5}).json()
    expect_event("dieline.computed")
    topo1 = post("/api/v1/packaging/panel-topology", json={"panels": die["panels"]}).json()
    expect_event("panel_topology.analyzed")

    check("L5 bridge: dieline now emits structured panels and says honestly that it emits only the "
          "finished face (no invented box unfolding), so the two engines are no longer islands",
          die["panel_count"] == len(die["panels"]) == 1
          and die["panel_basis"] == "trim_face_only"
          and die["panels"][0]["width"] == die["finished"]["width"]
          and die["panels"][0]["height"] == die["finished"]["height"]
          and die["trim_box"] == [0.0, 0.0, 100.0, 80.0]
          and bool(die.get("panel_note")),
          "panels=%s basis=%s" % (json.dumps(die["panels"], ensure_ascii=False), die["panel_basis"]))

    perim1 = union_rect_perimeter(die["panels"])
    check("L5 bridge: feeding dieline.panels VERBATIM into panel-topology yields a topology whose "
          "extent and cut-boundary equal the dieline's own finished size, and the perimeter matches "
          "an independent coordinate-compressed edge sweep (a second algorithm, not the engine's "
          "shared-edge arithmetic)",
          topo1["panel_count"] == die["panel_count"]
          and topo1["extent"]["width"] == die["finished"]["width"]
          and topo1["extent"]["height"] == die["finished"]["height"]
          and abs(topo1["cut_boundary_length"] - perim1) < 1e-6
          and abs(topo1["cut_boundary_length"] - 2 * (100 + 80)) < 1e-6
          and topo1["implemented"] is True and topo1["fabricated"] is False,
          "cut=%s independent_perim=%s extent=%s" % (topo1["cut_boundary_length"], perim1,
                                                      json.dumps(topo1["extent"])))

    # a real 2-panel box blank: two 100x80 faces sharing the x=100 edge
    two = [{"id": "left", "x": 0, "y": 0, "width": 100, "height": 80},
           {"id": "right", "x": 100, "y": 0, "width": 100, "height": 80}]
    topo2 = post("/api/v1/packaging/panel-topology", json={"panels": two}).json()
    expect_event("panel_topology.analyzed")
    perim2 = union_rect_perimeter(two)
    apart_check = 2 * (2 * (100 + 80)) - 2 * 80     # inclusion-exclusion: two faces minus the shared seam twice
    check("L5 bridge: a two-panel blank really produces ONE shared fold of exactly the shared edge "
          "length, a connected graph, and a cut boundary equal to the union outline — the bridge is "
          "not trivially satisfied by the single-panel case",
          topo2["panel_count"] == 2 and topo2["relation_count"] == 1
          and topo2["is_connected"] is True and topo2["status"] == "ok"
          and topo2["group_count"] == 1
          and abs(topo2["fold_length_total"] - 80.0) < 1e-6
          and abs(topo2["cut_boundary_length"] - perim2) < 1e-6
          and abs(topo2["cut_boundary_length"] - apart_check) < 1e-6
          and abs(topo2["cut_boundary_length"] - 560.0) < 1e-6
          and topo2["extent"]["width"] == 200.0 and topo2["extent"]["height"] == 80.0,
          "rel=%s fold=%s cut=%s independent=%s inclusion_exclusion=%s"
          % (topo2["relation_count"], topo2["fold_length_total"],
             topo2["cut_boundary_length"], perim2, apart_check))

    apart = [{"id": "a", "x": 0, "y": 0, "width": 50, "height": 50},
             {"id": "b", "x": 200, "y": 200, "width": 50, "height": 50}]
    topo3 = post("/api/v1/packaging/panel-topology", json={"panels": apart}).json()
    expect_event("panel_topology.analyzed")
    perim3 = union_rect_perimeter(apart)
    bad = post("/api/v1/packaging/panel-topology", json={"panels": []})
    check("L5 bridge negative controls: panels that do not touch are reported disconnected (never "
          "quietly 'ok'), their cut boundary is the SUM of both outlines (no phantom merge), and an "
          "empty panel list is an honest 400 rather than a fabricated topology",
          topo3["status"] == "disconnected" and topo3["is_connected"] is False
          and topo3["relation_count"] == 0 and topo3["group_count"] == 2
          and abs(topo3["cut_boundary_length"] - perim3) < 1e-6
          and abs(topo3["cut_boundary_length"] - 400.0) < 1e-6
          and topo3["fold_length_total"] == 0
          and bad.status_code == 400,
          "disconnected=%s groups=%s cut=%s independent=%s empty_http=%d"
          % (topo3["status"], topo3["group_count"], topo3["cut_boundary_length"], perim3,
             bad.status_code))

    # =====================================================================
    # 3. real cross-engine workflow graph, hop by hop
    # =====================================================================
    graph = {
        "id": "l5lineage", "name": "L5 dieline->topology->barcode",
        "description": "value lineage through three engine domains in one graph",
        "variables": {},
        "nodes": [
            {"id": "in", "type": "input", "name": "in", "config": {}},
            {"id": "die", "type": "tool", "name": "dieline",
             "config": {"tool_name": "packaging.dieline",
                        "parameters": {"width": 100, "height": 80, "bleed": 3, "safe": 5},
                        "output_variable": "die"}},
            {"id": "topo", "type": "tool", "name": "topology",
             "config": {"tool_name": "packaging.panel_topology",
                        "parameters": {"panels": "$die.panels"},
                        "output_variable": "topo"}},
            {"id": "bc", "type": "tool", "name": "barcode",
             "config": {"tool_name": "packaging.barcode",
                        "parameters": {"code": "4006381333931"},
                        "output_variable": "bc"}},
            {"id": "out", "type": "output", "name": "out",
             "config": {"select": ["die", "topo", "bc"]}},
        ],
        "edges": [{"source_node_id": "in", "target_node_id": "die"},
                  {"source_node_id": "die", "target_node_id": "topo"},
                  {"source_node_id": "topo", "target_node_id": "bc"},
                  {"source_node_id": "bc", "target_node_id": "out"}],
    }
    dres = post("/api/v1/workflow/define", json=graph).json()
    vres = get("/api/v1/workflow/l5lineage/validate").json()
    eres = post("/api/v1/workflow/l5lineage/execute", json={"input_data": {}}).json()
    expect_event("workflow.executed")
    expect_event("dieline.computed")
    expect_event("panel_topology.analyzed")
    expect_event("barcode.rendered")
    inst = get("/api/v1/workflow/instance/%s" % eres["instance_id"]).json()
    res = inst.get("result") or {}
    wf_die, wf_topo, wf_bc = res.get("die") or {}, res.get("topo") or {}, res.get("bc") or {}

    check("L5 graph: a 5-node graph spanning packaging -> packaging(topology) -> packaging(barcode) "
          "really executes, every node reports success, and the instance status is derived from the "
          "node statuses rather than asserted",
          dres.get("nodes") == 5 and dres.get("edges") == 4 and dres.get("validation_errors") == []
          and vres.get("errors") == []
          and inst.get("status") == "success"
          and set(inst.get("node_status") or {}) == {"in", "die", "topo", "bc", "out"}
          and all(v == "success" for v in (inst.get("node_status") or {}).values())
          and inst.get("error_message") is None,
          "status=%s nodes=%s" % (inst.get("status"), json.dumps(inst.get("node_status"))))

    check("L5 graph hop die->topo: the topology node consumed the dieline node's OWN panels (field "
          "equality against the upstream payload) and its geometry matches an independent perimeter "
          "recomputation — this is a lineage edge, not two calls that happened in order",
          wf_die.get("panels") == die["panels"]
          and wf_topo.get("panel_count") == wf_die.get("panel_count") == 1
          and abs(wf_topo.get("cut_boundary_length", -1) - union_rect_perimeter(wf_die["panels"])) < 1e-6
          and wf_topo.get("extent", {}).get("width") == wf_die.get("finished", {}).get("width"),
          "die_panels=%s topo_cut=%s independent=%s"
          % (json.dumps(wf_die.get("panels"), ensure_ascii=False), wf_topo.get("cut_boundary_length"),
             union_rect_perimeter(wf_die.get("panels") or [{"x": 0, "y": 0, "width": 0, "height": 0}])))

    code12 = wf_bc.get("code", "")[:-1]
    bcp = wf_bc.get("output_path")
    bc_bytes = Path(str(bcp)).stat().st_size if bcp and Path(str(bcp)).exists() else None
    check("L5 graph hop ->barcode: the rendered EAN-13 check digit equals a GS1 modulo-10 recomputation "
          "done inside this test, the module count equals the bit-pattern length, and the PNG the "
          "workflow claims to have written really exists on disk with the reported byte count",
          wf_bc.get("check_digit") == ean13_check_digit(code12)
          and wf_bc.get("module_count") == len(wf_bc.get("bit_pattern") or "")
          and wf_bc.get("status") == "ok" and wf_bc.get("fabricated") is False
          and bc_bytes == wf_bc.get("bytes") and bc_bytes > 0,
          "code=%s check=%s recomputed=%s modules=%s bytes=%s"
          % (wf_bc.get("code"), wf_bc.get("check_digit"), ean13_check_digit(code12),
             wf_bc.get("module_count"), bc_bytes))

    # --- path reference negative controls ---
    bad_graph = json.loads(json.dumps(graph))
    bad_graph["id"] = "l5badpath"
    bad_graph["nodes"][2]["config"]["parameters"]["panels"] = "$die.no_such_field"
    post("/api/v1/workflow/define", json=bad_graph)
    b1 = post("/api/v1/workflow/l5badpath/execute", json={"input_data": {}}).json()
    expect_event("workflow.executed")
    # the dieline node SUCCEEDS before the broken path is consumed downstream, so its
    # event really fires here too — the ledger count must reflect that, not the happy path only
    expect_event("dieline.computed")
    b1_inst = get("/api/v1/workflow/instance/%s" % b1["instance_id"]).json()
    err1 = str(b1_inst.get("error_message") or "")
    real_keys = sorted((b1_inst.get("node_outputs") or {}).get("die", {}).get("result", {}).keys()) \
        if isinstance((b1_inst.get("node_outputs") or {}).get("die"), dict) else []

    bad_graph2 = json.loads(json.dumps(graph))
    bad_graph2["id"] = "l5ghosttool"
    bad_graph2["nodes"][1]["config"]["tool_name"] = "packaging.not_a_tool"
    post("/api/v1/workflow/define", json=bad_graph2)
    b2 = post("/api/v1/workflow/l5ghosttool/execute", json={"input_data": {}}).json()
    expect_event("workflow.executed")
    b2_inst = get("/api/v1/workflow/instance/%s" % b2["instance_id"]).json()

    check("L5 graph negative controls: a broken path reference fails the node loudly and the error "
          "names the keys the upstream hop ACTUALLY produced (proving it read the real payload, not a "
          "guess); an unregistered tool fails too; neither degrades to a silent None that would make "
          "a severed lineage look like a successful one",
          b1_inst.get("status") == "failed"
          and (b1_inst.get("node_status") or {}).get("topo") == "failed"
          and "no_such_field" in err1 and "panels" in err1
          and b2_inst.get("status") == "failed"
          and "not_a_tool" in str(b2_inst.get("error_message") or "")
          and (b1_inst.get("result") or {}).get("topo") is None,
          "path_err=%r ghost_err=%r upstream_keys=%s"
          % (err1[:110], str(b2_inst.get("error_message") or "")[:60], real_keys[:6]))

    # --- integer index path reference, with a real font index behind it ---
    scan = post("/api/v1/typography/scan?limit=3", timeout=900).json()
    expect_event("fonts.scanned")
    idx_graph = {
        "id": "l5index", "name": "L5 index path ref",
        "variables": {},
        "nodes": [
            {"id": "in", "type": "input", "name": "in", "config": {}},
            {"id": "fonts", "type": "tool", "name": "fonts",
             "config": {"tool_name": "typography.fonts", "parameters": {}, "output_variable": "fl"}},
            {"id": "met", "type": "tool", "name": "metrics",
             "config": {"tool_name": "typography.metrics",
                        "parameters": {"font": "$fl.fonts.0.postscript_name",
                                       "text": "AV", "size_pt": 12},
                        "output_variable": "met"}},
            {"id": "out", "type": "output", "name": "out", "config": {"select": ["fl", "met"]}},
        ],
        "edges": [{"source_node_id": "in", "target_node_id": "fonts"},
                  {"source_node_id": "fonts", "target_node_id": "met"},
                  {"source_node_id": "met", "target_node_id": "out"}],
    }
    post("/api/v1/workflow/define", json=idx_graph)
    i_res = post("/api/v1/workflow/l5index/execute", json={"input_data": {}}).json()
    expect_event("workflow.executed")
    i_inst = get("/api/v1/workflow/instance/%s" % i_res["instance_id"]).json()
    i_out = i_inst.get("result") or {}
    fl = i_out.get("fl") or {}
    met = i_out.get("met") or {}
    fam0 = (fl.get("fonts") or [{}])[0].get("family")
    ref0 = (fl.get("fonts") or [{}])[0].get("postscript_name")
    # Independent recomputation of the lineage edge: hand the SAME reference string to the
    # metrics endpoint over plain HTTP and require the workflow hop's payload to be
    # byte-for-byte that response. If the index path reference had carried a guessed or
    # fabricated name, the two would not agree (and the direct call would 404).
    direct_met = post("/api/v1/typography/metrics",
                      json={"font": ref0, "text": "AV", "size_pt": 12}).json() if ref0 else {}
    check("L5 graph: integer-index path references ($fl.fonts.0.postscript_name) resolve against a "
          "real font index built by a preceding scan, and the metrics hop independently resolves that "
          "same string back to the SAME family the index recorded — proven by replaying the reference "
          "against the metrics endpoint directly and getting an identical payload back (scan -> "
          "typography lineage across engines, not two coincidental calls)",
          scan.get("scanned", 0) >= 1 and fl.get("total", 0) >= 1
          and i_inst.get("status") == "success"
          and bool(ref0) and bool(fam0) and met.get("font") == fam0
          and (met.get("width_pt") or 0) > 0
          and met.get("text") == "AV" and met.get("size_pt") == 12
          and met.get("glyph_count") == 2 and met.get("missing_glyphs") == 0
          and met.get("fully_covered") is True
          and met == direct_met,
          "scanned=%s ref0=%s family0=%s metrics_font=%s width_pt=%s direct_equal=%s err=%r"
          % (scan.get("scanned"), ref0, fam0, met.get("font"), met.get("width_pt"),
             met == direct_met,
             str(i_inst.get("error_message") or "")[:90]))

    # =====================================================================
    # 3b. container-position references — the journey's last mile (Phase 54)
    # =====================================================================
    # Measured by the read-only journey spike on a fresh data root: a delivery
    # node whose parameters carry '$bc.output_path' *inside a list* reaches
    # status=success with included=0, skipped=[{"path": "$bc.output_path",
    # "reason": "missing_or_blocked"}] and a 619-byte archive containing only
    # manifest.json + README.txt, while the same graph with a literal path in the
    # same position produces a 1078-byte archive with the PNG inside. The value
    # died inside the container and every self-reported success signal stayed
    # green — so these checks grade the ARCHIVE BYTES (unzipped and hashed here),
    # never the endpoint's own `included` counter.
    def _p54_graph(gid, files, barcode=True):
        nodes = [{"id": "in", "type": "input", "name": "in", "config": {}}]
        edges = []
        if barcode:
            nodes.append({"id": "bc", "type": "tool", "name": "barcode",
                          "config": {"tool_name": "packaging.barcode",
                                     "parameters": {"code": "4006381333931"},
                                     "output_variable": "bc"}})
            edges.append({"source_node_id": "in", "target_node_id": "bc"})
            src_to_dl, sel = "bc", ["bc", "dl"]
        else:
            src_to_dl, sel = "in", ["dl"]
        nodes.append({"id": "dl", "type": "tool", "name": "delivery",
                      "config": {"tool_name": "delivery.package",
                                 "parameters": {"project": "l5p54", "files": files},
                                 "output_variable": "dl"}})
        nodes.append({"id": "out", "type": "output", "name": "out",
                      "config": {"select": sel}})
        edges += [{"source_node_id": src_to_dl, "target_node_id": "dl"},
                  {"source_node_id": "dl", "target_node_id": "out"}]
        return {"id": gid, "name": "L5 P54 container refs " + gid, "variables": {},
                "nodes": nodes, "edges": edges}

    def _p54_single(gid, tool, params):
        return {"id": gid, "name": "L5 P54 single " + gid, "variables": {},
                "nodes": [{"id": "in", "type": "input", "name": "in", "config": {}},
                          {"id": "t", "type": "tool", "name": tool,
                           "config": {"tool_name": tool, "parameters": params,
                                      "output_variable": "t"}},
                          {"id": "out", "type": "output", "name": "out",
                           "config": {"select": ["t"]}}],
                "edges": [{"source_node_id": "in", "target_node_id": "t"},
                          {"source_node_id": "t", "target_node_id": "out"}]}

    def _p54_zip(archive):
        p = Path(str(archive or ""))
        if not archive or not p.is_file():
            return None
        with zipfile.ZipFile(str(p)) as zf:
            names = sorted(zf.namelist())
            digests = {n: hashlib.sha256(zf.read(n)).hexdigest() for n in names}
        return {"names": names, "bytes": p.stat().st_size, "sha256": digests}

    def _p54_names_path(err, p):
        # Does the rejection name the RESOLVED value rather than the raw reference?
        # pydantic truncates long input values inside str(ValidationError) — measured
        # by p54_err_probe.py: the head and a >=12-char tail survive, the middle
        # becomes an ellipsis, so a whole-path match could never be satisfied by any
        # product. This test therefore pins the tail of the resolved file name, and
        # the caller pairs it with the ABSENCE of the literal '$bc.' — a FOUND and an
        # ABSENT nail together, because either one alone can be faked.
        if not p:
            return False
        frag = Path(p).name[-8:]
        return bool(frag) and frag in str(err)

    _p54_files = {"s1_scalar_ref": "$bc.output_path",
                  "s2_list_element": {"Print": ["$bc.output_path"]},
                  "s3_dict_value": {"Print": "$bc.output_path"}}
    runs = {}
    literal_path = None
    for shape in ("s1_scalar_ref", "s2_list_element", "s3_dict_value", "s4_literal_paths"):
        if shape == "s4_literal_paths":
            files = {"Print": [literal_path]} if literal_path else None
        else:
            files = _p54_files[shape]
        gid = "l5p54_" + shape
        post("/api/v1/workflow/define", json=_p54_graph(gid, files))
        ex = post("/api/v1/workflow/%s/execute" % gid, json={"input_data": {}}).json()
        expect_event("workflow.executed")
        expect_event("barcode.rendered")
        # delivery.created fires for exactly two of the four shapes — s2 and s4 —
        # and that count is a property of the CONTRACT (only the two well-typed
        # shapes can reach the endpoint), not of this bug, so the tally is pinned
        # once here rather than derived from the behaviour under test.
        if shape in ("s2_list_element", "s4_literal_paths"):
            expect_event("delivery.created")
        r_inst = get("/api/v1/workflow/instance/%s" % ex["instance_id"]).json()
        r_no = r_inst.get("node_outputs") or {}
        r_bc = (r_no.get("bc") or {}).get("result") or {}
        r_dl = (r_no.get("dl") or {}).get("result") or {}
        if r_bc.get("output_path") and literal_path is None:
            literal_path = str(r_bc["output_path"])
        runs[shape] = {"status": r_inst.get("status"),
                       "ns": r_inst.get("node_status") or {},
                       "err": str(r_inst.get("error_message") or ""),
                       "bc": r_bc, "dl": r_dl, "zip": _p54_zip(r_dl.get("archive"))}

    base_name = Path(literal_path).name if literal_path else ""
    disk_sha = sha256_file(literal_path) if literal_path and Path(literal_path).is_file() else None

    def _shape_fact(shape):
        r = runs[shape]
        return {shape: {"status": r["status"], "dl": r["ns"].get("dl"),
                        "included": r["dl"].get("included"),
                        "skipped_n": len(r["dl"].get("skipped") or []),
                        "err_resolved": _p54_names_path(r["err"], literal_path),
                        "err_still_ref": "$bc." in r["err"],
                        "err": r["err"][:80]}}

    s1, s2, s3, s4 = (runs["s1_scalar_ref"], runs["s2_list_element"],
                      runs["s3_dict_value"], runs["s4_literal_paths"])
    ok_s1 = (s1["status"] == "failed" and s1["ns"].get("dl") == "failed"
             and "dict_type" in s1["err"] and _p54_names_path(s1["err"], literal_path)
             and "$bc." not in s1["err"])
    ok_s2 = (s2["status"] == "success" and s2["dl"].get("included") == 1
             and s2["dl"].get("skipped") == [] and s2["dl"].get("sha256_manifest"))
    ok_s3 = (s3["status"] == "failed" and s3["ns"].get("dl") == "failed"
             and "list_type" in s3["err"] and _p54_names_path(s3["err"], literal_path)
             and "$bc." not in s3["err"])
    ok_s4 = (s4["status"] == "success" and s4["dl"].get("included") == 1
             and s4["dl"].get("skipped") == [])
    check("L5 P54 four shapes: every position a reference can sit in resolves to the "
          "SAME value a literal would take — scalar (s1, well-formed but wrong type: "
          "rejected loudly and the rejection names the RESOLVED path, not the raw "
          "reference), list element (s2, the journey's real shape: must succeed and "
          "include the file), dict value (s3, wrong type for a list: rejected loudly "
          "with the resolved path), literal (s4, the control that always worked) — "
          "a reference that quietly turns into a missing file is a severed lineage "
          "wearing a success badge",
          ok_s1 and ok_s2 and ok_s3 and ok_s4,
          "literal=%s s1=%s s2=%s s3=%s s4=%s"
          % (bool(literal_path), json.dumps(_shape_fact("s1_scalar_ref"), ensure_ascii=False),
             json.dumps(_shape_fact("s2_list_element"), ensure_ascii=False),
             json.dumps(_shape_fact("s3_dict_value"), ensure_ascii=False),
             json.dumps(_shape_fact("s4_literal_paths"), ensure_ascii=False)))

    z2, z4 = s2["zip"], s4["zip"]
    want_names = ["Print/" + base_name, "README.txt", "manifest.json"] if base_name else None
    check("L5 P54 archive measured, not self-reported: the ZIP the s2 graph points at "
          "really contains Print/<barcode png> whose sha256 equals this test hashing the "
          "file on disk, its namelist is exactly the payload plus manifest and README, the "
          "endpoint's included counter agrees with the payload entries counted here, and the "
          "reported byte size equals the file size — the 619-byte manifest-only archive the "
          "spike measured is the failure this check refuses to call success",
          z2 is not None and z4 is not None and disk_sha is not None
          and z2["names"] == want_names
          and z2["sha256"]["Print/" + base_name] == disk_sha
          and z4["names"] == want_names
          and z4["sha256"]["Print/" + base_name] == disk_sha
          and s2["dl"].get("included") == len([n for n in z2["names"]
                                               if n not in ("manifest.json", "README.txt")])
          and s2["dl"].get("archive_size_bytes") == z2["bytes"],
          "names_s2=%s sha_equal=%s included=%s payload_count=%s bytes=%s/%s manifest_sha=%s"
          % (json.dumps(z2["names"] if z2 else None),
             bool(z2 and disk_sha and z2["sha256"].get("Print/" + base_name) == disk_sha),
             s2["dl"].get("included"),
             len([n for n in (z2 or {}).get("names", [])
                  if n not in ("manifest.json", "README.txt")]),
             (s2["dl"] or {}).get("archive_size_bytes"), z2 and z2["bytes"],
             (s2["dl"].get("sha256_manifest") or {}).get("Print")))

    dl_dir = DATA / "deliveries"
    before_dl = set(os.listdir(dl_dir)) if dl_dir.exists() else set()
    ghost_gid = "l5p54_ghost_root"
    post("/api/v1/workflow/define", json=_p54_graph(ghost_gid, {"Print": ["$ghostly.output_path"]},
                                                    barcode=False))
    gx = post("/api/v1/workflow/%s/execute" % ghost_gid, json={"input_data": {}}).json()
    expect_event("workflow.executed")
    g_inst = get("/api/v1/workflow/instance/%s" % gx["instance_id"]).json()
    g_err = str(g_inst.get("error_message") or "")
    g_ns = g_inst.get("node_status") or {}
    after_dl = set(os.listdir(dl_dir)) if dl_dir.exists() else set()
    check("L5 P54 unresolved reference is a failure, never an empty delivery: a container "
          "position naming a root variable that no upstream node produced must fail the node, "
          "name the missing root ('ghostly') in the error, and leave the deliveries directory "
          "byte-for-byte untouched — the spike's behaviour (success + included=0 + a fresh "
          "manifest-only archive) is the fake success this check refuses to accept",
          g_inst.get("status") == "failed" and g_ns.get("dl") == "failed"
          and "ghostly" in g_err and after_dl == before_dl,
          "status=%s dl=%s err=%r new_archives=%s"
          % (g_inst.get("status"), g_ns.get("dl"), g_err[:120], sorted(after_dl - before_dl)))

    gram_gid = "l5p54_grammar"
    post("/api/v1/workflow/define", json=_p54_single(gram_gid, "color.convert",
                                                     {"hex": "$1,200"}))
    gm = post("/api/v1/workflow/%s/execute" % gram_gid, json={"input_data": {}}).json()
    expect_event("workflow.executed")
    m_inst = get("/api/v1/workflow/instance/%s" % gm["instance_id"]).json()
    m_err = str(m_inst.get("error_message") or "")
    check("L5 P54 reference grammar: '$' only introduces a reference when it is followed by a "
          "variable name — '$1,200' is a literal and must reach the tool verbatim, so the color "
          "engine rejects it as an illegal colour while quoting the very string the user wrote. "
          "Silently mapping $-prefixed non-references to None would make a typo look like a "
          "missing variable, and a price tag like '$1,200' is a legal parameter value",
          m_inst.get("status") == "failed"
          and (m_inst.get("node_status") or {}).get("t") == "failed"
          and "$1,200" in m_err,
          "status=%s err=%r" % (m_inst.get("status"), m_err[:140]))


    # =====================================================================
    # 4. batch shares the single-item path
    # =====================================================================
    img = tmp / "l5_red.png"
    _im = Image.new("RGB", (120, 120), (255, 255, 255))
    for x in range(45, 75):
        for y in range(45, 75):
            _im.putpixel((x, y), (220, 30, 30))
    _im.save(img)

    single = post("/api/v1/generate/remove-background", json={"image_path": str(img)}).json()
    expect_event("bg_removed")
    batched = post("/api/v1/batch/process",
                   json={"file_paths": [str(img)], "operation": "remove_background",
                         "workers": 2}).json()
    expect_event("batch.completed")
    expect_event("bg_removed")
    b0 = batched["results"][0]
    bo = Path(str(b0.get("output")))
    with Image.open(bo) as _bo:
        rgba = _bo.convert("RGBA")
        corner = rgba.getpixel((0, 0))
        center = rgba.getpixel((60, 60))

    check("L5 batch: the batch path no longer carries a second weaker implementation — its algorithm "
          "string, cleared-pixel ratio and output pixels are identical to the single-item endpoint, "
          "the background really became transparent at the corner and stayed opaque at the subject, "
          "and the old 'use the real endpoint' hint is gone",
          b0.get("algorithm") == single.get("algorithm")
          and b0.get("background_cleared_ratio") == single.get("background_cleared_ratio")
          and "hint" not in b0 and bo.exists()
          and rgba.mode == "RGBA" and corner[3] == 0 and center[3] == 255
          and batched["success"] == 1 and batched["failed"] == 0,
          "algo=%s ratio=%s vs single=%s corner_alpha=%s center_alpha=%s"
          % (b0.get("algorithm"), b0.get("background_cleared_ratio"),
             single.get("background_cleared_ratio"), corner[3], center[3]))

    ba = post("/api/v1/batch/process",
              json={"file_paths": [str(img)], "operation": "import_asset", "workers": 2}).json()
    expect_event("batch.completed")
    expect_event("asset.ingested")
    bid = ba["results"][0].get("asset_id")
    got = get("/api/v1/assets/%s" % bid) if bid else None
    ghost = post("/api/v1/batch/process",
                 json={"file_paths": [str(tmp / "missing.png")], "operation": "import_asset",
                       "workers": 1}).json()
    expect_event("batch.completed")
    check("L5 batch: import_asset through the batch path now goes through the real endpoint (so the "
          "asset is indexed and retrievable by id afterwards, and the ingest event fires), while a "
          "missing file is still an honest per-file failure instead of a batch-level 500",
          bid and got is not None and got.status_code == 200
          and ghost["success"] == 0 and ghost["failed"] == 1
          and ghost["results"][0]["success"] is False
          and bool(ghost["results"][0].get("error")),
          "asset_id=%s get=%s ghost=%s" % (bid, got.status_code if got else None,
                                           json.dumps(ghost["results"][0], ensure_ascii=False)[:110]))

    # 本轮真实回归：批量改走单件端点后，印前分支一度把"引擎自报检查失败"的报告当成
    # success=True（缺文件在 HTTP 层是 200 + PRINT-999 报告）。产品已修，此处锁死口径。
    #
    # Phase 55 就地改核（旧口径被**故意**废止，非放松）：修前这一族的唯一失败信号是引擎
    # 自报的中文消息串，所以旧判词钉的是 "不存在或不可读" in error，并且必须
    # expect_event("preflight.completed")——因为 _emit 早于批处理层的串校验，账本确实记了
    # 这一条。现在 /preflight 入口接上全仓共用的 validate_file_path，门在引擎之前：缺件根本
    # 走不进引擎，于是 (a) 逐文件错误串改成门的话"文件不存在"，(b) 账本不再为没发生的检查
    # 记 completed——此处因此**不再**调用 expect_event，若产品仍发布该事件，末尾的
    # triggered×kind_counts 关系式会自动变红（负判据由机制保证，不靠人记）。
    pfg = post("/api/v1/batch/process",
               json={"file_paths": [str(tmp / "missing-preflight.png")],
                     "operation": "preflight", "workers": 1}).json()
    expect_event("batch.completed")
    check("L5 batch(P55): a missing document on the preflight path is refused by the SAME gate the rest of the service uses, so the per-file error is the transport verdict (404 文件不存在) rather than a diagnosis string the batch layer had to pattern-match, and the batch aggregate follows the per-file verdicts instead of counting returned coroutines — this replaces the P51 wording, which could only point at the engine's own Chinese message because no gate existed upstream of it",
          pfg["total_files"] == 1 and pfg["success"] == 0 and pfg["failed"] == 1
          and pfg["results"][0]["success"] is False
          and "文件不存在" in str(pfg["results"][0].get("error")),
          json.dumps(pfg["results"][0], ensure_ascii=False)[:160])

    # 值血缘第二跳：门在**单件端点**上分出的两种形制，穿过批处理聚合后必须仍可区分。
    # 修前实测（p55_pre_fix_live.json batch.per_file）：幽灵路径与穿越路径的错误串**逐字节
    # 相同**（都是"检查失败：文档不存在或不可读：<path>"），也就是说"这个文件是没了"还是
    # "这个文件被安全策略禁入"——两种责任完全不同的输入——在链路里被压成了同一个词。
    _p55_trav = str(DATA / ".." / ".." / "windows" / "win.ini")
    bg55 = post("/api/v1/batch/process",
                json={"file_paths": [str(img), str(tmp / "p55_ghost.png"), _p55_trav],
                      "operation": "preflight", "workers": 1}).json()
    expect_event("batch.completed")
    expect_event("preflight.completed")   # 只有真被检成的那一个文件该记 completed
    _rows55 = {r["file"]: r for r in (bg55.get("results") or [])}
    _ok55 = _rows55.get(str(img)) or {}
    check("L5 batch(P55) lineage hop: the distinction the gate makes on the single endpoint SURVIVES the batch aggregate — the real document succeeds and carries the reachability verdict produced by the endpoint (size_bytes equal to this test hashing the same file's bytes off disk), while the ghost and the blocked path fail with two DIFFERENT verdicts (404 文件不存在 vs 403 安全策略), so no consumer downstream has to re-guess which kind of input it was handed",
          bg55["success"] == 1 and bg55["failed"] == 2
          and _ok55.get("success") is True
          and (_ok55.get("reachability") or {}).get("verdict") == "checked"
          and (_ok55.get("reachability") or {}).get("size_bytes") == Path(str(img)).stat().st_size
          and "文件不存在" in str(_rows55.get(str(tmp / "p55_ghost.png"), {}).get("error"))
          and "安全策略" in str(_rows55.get(_p55_trav, {}).get("error")),
          "rows=%s size=%s" % (json.dumps(bg55.get("results"), ensure_ascii=False)[:260],
                               Path(str(img)).stat().st_size))

    # =====================================================================
    # 5. event ledger — runtime derived, exactly counted
    # =====================================================================
    # trigger the two remaining side-effect kinds the chain above did not reach
    aid_for_delete = bid
    ddel = requests.delete(BASE + "/api/v1/assets/%s" % aid_for_delete, timeout=60)
    expect_event("asset.deleted")
    post("/api/v1/recovery/task/register", json={"task_id": "l5failtask", "total_steps": 3})
    expect_event("task.registered")
    post("/api/v1/recovery/task/l5failtask/fail",
         json={"error_message": "L5 deliberate failure", "recoverable": True})
    expect_event("task.failed")

    # write-side ops that must also be observable
    docp = tmp / "l5_doc.pdf"
    docp.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
    post("/api/v1/preflight", json={"document_path": str(docp)}); expect_event("preflight.completed")
    post("/api/v1/preflight/auto-fix", json={"document_path": str(docp)}); expect_event("preflight.autofixed")
    requests.put(BASE + "/api/v1/document", params={"name": "l5-renamed.psd"}, timeout=60)
    expect_event("document.renamed")
    lc = post("/api/v1/layers", json={"name": "L5 Layer", "type": "text"}).json()
    expect_event("layer.created")
    # P65-D1 修复后：L3 删除须显式 confirm。负形（不带 confirm）先跑一次，
    # 事件账本必须**没有**这次删除——拒绝即无副作用，这是 L5 面独有的可观测判据。
    _p65_ref = requests.delete(BASE + "/api/v1/layers/%s" % lc["id"], timeout=60)
    _p65_ev = [e for e in (get("/api/v1/events/recent", params={"limit": 2000},
                               timeout=60).json().get("events") or [])
               if e.get("event") == "layer.deleted"
               and (e.get("data") or {}).get("layer_id") == lc["id"]]
    check("l5 P65 gate: DELETE refused without confirm emits NO layer.deleted event and the layer survives (拒绝即无副作用；此判据不看 deleted 标志，独立数账本)",
          _p65_ref.status_code == 400 and "confirmation_required" in _p65_ref.text
          and len(_p65_ev) == 0
          and any(l["id"] == lc["id"] for l in get("/api/v1/layers", timeout=60).json()),
          "status=%s ev=%s" % (_p65_ref.status_code, len(_p65_ev)))
    requests.delete(BASE + "/api/v1/layers/%s" % lc["id"], params={"confirm": "true"}, timeout=60); expect_event("layer.deleted")
    sn = post("/api/v1/recovery/snapshot",
              json={"document_path": str(docp), "snapshot_type": "incremental"}).json()
    expect_event("snapshot.created")
    post("/api/v1/recovery/snapshot/%s/restore" % sn["id"]); expect_event("snapshot.restored")
    post("/api/v1/recovery/task/register", json={"task_id": "l5oktask", "total_steps": 2})
    expect_event("task.registered")
    post("/api/v1/recovery/task/l5oktask/progress", json={"current_step": 1})
    expect_event("task.progress")
    post("/api/v1/recovery/task/l5oktask/complete"); expect_event("task.completed")
    kb = tmp / "l5_kb.md"
    kb.write_text("# Brand\n\noffset printing needs 3mm bleed and 5mm safe margin.\n", encoding="utf-8")
    post("/api/v1/knowledge/ingest",
         json={"file_path": str(kb), "title": "L5 note", "scope": "project", "project_id": "l5"})
    expect_event("knowledge.ingested")
    post("/api/v1/review/run", json={
        "objects": [{"id": "t", "type": "text", "x": 0, "y": 0, "width": 100, "height": 20,
                     "text": "hello", "font_size": 6, "color": "#777777"}],
        "canvas": {"width": 1000, "height": 1000, "bleed": 0, "dpi": 300, "background": "#FFFFFF"},
        "brand": {"palette": ["#FF0000"]}})
    expect_event("review.completed")
    rp = post("/api/v1/revision/parse", json={
        "text": "make the logo bigger",
        "objects": [{"id": "logo", "type": "image", "x": 0, "y": 0, "width": 40, "height": 40}]}).json()
    post("/api/v1/revision/apply", json={
        "objects": [{"id": "logo", "type": "image", "x": 0, "y": 0, "width": 40, "height": 40}],
        "changes": rp.get("changes") or []})
    expect_event("revision.applied")
    post("/api/v1/delivery/package",
         json={"project": "l5", "files": {"Source": [str(kb)], "Preview": [str(img)]}})
    expect_event("delivery.created")
    post("/api/v1/packaging/barcode", json={"code": "4006381333931"})
    expect_event("barcode.rendered")

    # ---------------- Phase 55 可达性台账的血缘跳 ----------------
    # 交付面是这条链的终点：用户把设计稿交给 /delivery/package，得到的是"包里到底有什么、
    # 每样东西从哪来"。修前实测（p55_delivery_500.json）：一个目录或空串输入让整个请求 500，
    # 而"文件不存在"与"安全策略禁入"共用一枚 missing_or_blocked——终点把两种责任压成一个词，
    # 上游门分得再清也在这里丢。此处按血缘钉：/health.data_root（服务自报的根）→ 响应里逐文件
    # 的 inside_data_root → ZIP 里真实存在的条目 → delivery.created 的计数，四者必须同源。
    _d55_in = DATA / "p55_l5_inside.png"
    _d55_in.write_bytes(Path(str(img)).read_bytes())
    _t55_trav = str(DATA / ".." / ".." / "windows" / "win.ini")
    _r55dl = post("/api/v1/delivery/package", timeout=300, json={
        "project": "l5-p55",
        "files": {"Print": [str(_d55_in), str(img)],
                  "Source": [str(DATA / "p55_l5_absent.png"), _t55_trav, str(DATA), ""]}})
    expect_event("delivery.created")
    # INSTRUMENT HARDENING (P55): pre-fix, this very payload answers 500 "Internal
    # Server Error" in PLAIN TEXT (a directory / empty-string input reaches shutil.
    # copy2 and raises PermissionError — p55_delivery_500.json holds the traceback),
    # so an unguarded .json() aborted the whole layer BEFORE the last two checks and
    # the event-ledger grading. The predicates below are untouched; only the body
    # capture is made total, so the pre-fix shape is reported as one honest FAIL
    # instead of a Python traceback (same family as P53's `health = {}` fix).
    try:
        _j55dl = _r55dl.json()
    except Exception:
        _j55dl = {"_non_json": True, "_http": _r55dl.status_code,
                  "_body": _r55dl.text[:200]}
    _z55dl = _p54_zip(_j55dl.get("archive"))
    _root55 = Path(str(health.get("data_root") or DATA)).resolve()

    def _in55(p):
        try:
            return Path(str(p)).resolve().is_relative_to(_root55)
        except Exception:
            return False

    _led55 = {e.get("name"): e.get("inside_data_root") for e in (_j55dl.get("included_files") or [])}
    _ev55dl = [e for e in (get("/api/v1/events/recent", params={"limit": 2000},
                               timeout=60).json().get("events") or [])
               if e.get("event") == "delivery.created"
               and (e.get("data") or {}).get("package_id") == _j55dl.get("package_id")]
    check("L5 delivery(P55) lineage: the reachability verdict survives every hop to the end of the chain — the per-file inside_data_root flags equal this test's own recomputation from the data root the service reported at /health, the outside file is LABELLED and still physically present in the ZIP (如实标注，不拦), the payload entries counted out of the namelist equal the self-reported included count, the four rejected inputs split across not_found/blocked_by_policy/not_a_file instead of one merged word, and delivery.created's own tally agrees with the response it was emitted from",
          _in55(_d55_in) is True and _in55(img) is False
          and _led55 == {Path(_d55_in).name: True, Path(img).name: False}
          and _j55dl.get("included") == 2 and _z55dl is not None
          and {"Print/" + Path(_d55_in).name, "Print/" + Path(img).name} <= set(_z55dl["names"])
          and len([n for n in _z55dl["names"] if not n.endswith(("README.txt", "manifest.json"))]) == 2
          and {s.get("reason") for s in (_j55dl.get("skipped") or [])}
              == {"not_found", "blocked_by_policy", "not_a_file"}
          and len(_j55dl.get("skipped") or []) == 4
          and _j55dl.get("outside_root_count") == 1
          and len(_ev55dl) == 1
          and (_ev55dl[0].get("data") or {}).get("outside_root_count") == 1,
          "led=%s reasons=%s zip=%s ev=%s"
          % (json.dumps(_led55, ensure_ascii=False),
             json.dumps(sorted(str(s.get("reason")) for s in (_j55dl.get("skipped") or []))),
             json.dumps(_z55dl["names"] if _z55dl else None),
             json.dumps(_ev55dl, ensure_ascii=False)[:180]))

    ev = get("/api/v1/events/recent", params={"limit": 2000}).json()
    kc = ev.get("kind_counts") or {}
    # The two COVERAGE checks are graded at the very end (section 9), because two of the
    # 26 declared kinds (asset.license_set, eval.completed) are only triggered by the
    # write-side and zero-drift sections further down. Grading the kind set here would
    # report them as "never triggered" — a scoring artifact, not a product defect.
    unsafe = []
    for e in (ev.get("events") or []):
        for k, v in (e.get("data") or {}).items():
            if isinstance(v, (set, tuple, bytes, Path)):
                unsafe.append((e.get("event"), k, type(v).__name__))
    recount = Counter(e["event"] for e in (ev.get("events") or []))
    check("L5 ledger: the ring buffer is serialisable by construction (no Path/enum/set leaked into an "
          "observation endpoint that would turn a read-only ledger into a 500), it self-describes "
          "its own capacity and truncation state, and its per-kind summary agrees with its own payload",
          json.loads(json.dumps(ev, ensure_ascii=False)) == ev and not unsafe
          and isinstance(ev.get("history_capacity"), int) and isinstance(ev.get("truncated"), bool)
          and {k: v for k, v in kc.items()} == dict(recount)
          and sorted(ev) == ["clients", "events", "history_capacity", "kind_count", "kind_counts",
                             "kinds_seen", "published_total", "total", "truncated"],
          "unsafe=%s keys=%s kc_vs_recount=%s"
          % (unsafe[:3], sorted(ev),
             {k: [kc.get(k), recount.get(k)] for k in set(kc) | set(recount)
              if kc.get(k) != recount.get(k)}))

    wf_evs = [e for e in ev["events"] if e["event"] == "workflow.executed"]
    lin_evs = [e for e in wf_evs if e["data"].get("workflow_id") == "l5lineage"]
    die_evs = [e for e in ev["events"] if e["event"] == "dieline.computed"]
    topo_evs = [e for e in ev["events"] if e["event"] == "panel_topology.analyzed"]
    check("L5 ledger: the two pure-computation bridge hops are observable as events that carry the "
          "lineage evidence themselves (dieline panel_basis/panel_count, topology input_panel_count "
          "and cut length), and the graph execution event names the tools it really used",
          die_evs and die_evs[-1]["data"].get("panel_basis") == "trim_face_only"
          and die_evs[-1]["data"].get("panel_count") == 1
          and topo_evs and topo_evs[-1]["data"].get("input_panel_count") == 1
          and abs(topo_evs[-1]["data"].get("cut_boundary_length", -1) - 360.0) < 1e-6
          and lin_evs and set(lin_evs[0]["data"].get("tools_used") or []) ==
          {"packaging.dieline", "packaging.panel_topology", "packaging.barcode"}
          and lin_evs[0]["data"].get("nodes_completed") == 5
          and lin_evs[0]["data"].get("status") == "success",
          "tools_used=%s nodes_completed=%s topo_cut=%s"
          % ((lin_evs[0]["data"].get("tools_used") if lin_evs else None),
             (lin_evs[0]["data"].get("nodes_completed") if lin_evs else None),
             (topo_evs[-1]["data"].get("cut_boundary_length") if topo_evs else None)))

    # =====================================================================
    # 6. retrieval ceiling is behaviourally true
    # =====================================================================
    srch = post("/api/v1/assets/search", json={"query": "red product", "top_k": 3}).json()
    kret = post("/api/v1/knowledge/retrieve",
                json={"query": "offset printing bleed", "top_k": 5}).json()
    kzh = post("/api/v1/knowledge/retrieve",
               json={"query": "\u5370\u5237\u51fa\u8840", "top_k": 5}).json()

    def _desc_ok(d):
        return (isinstance(d, dict) and d.get("semantic") is False
                and d.get("kind") == "sparse-token-hash-projection"
                and d.get("model") == "tokenhash-sha256-512" and d.get("dims") == 512
                and bool(d.get("note")))

    check("L5 retrieval ceiling: BOTH retrieval surfaces declare the ceiling they actually have "
          "(sparse SHA-256 token-hash projection, semantic=false, model+dims read from the live "
          "embedding) instead of implying a semantic embedding model",
          _desc_ok(srch.get("retrieval")) and _desc_ok(kret.get("retrieval")),
          "assets=%s knowledge=%s" % (json.dumps(srch.get("retrieval"), ensure_ascii=False)[:150],
                                       json.dumps(kret.get("retrieval"), ensure_ascii=False)[:80]))

    literal_hits = [r for r in kret.get("results") or [] if r.get("similarity", 0) > 0]
    zh_hits = [r for r in kzh.get("results") or [] if r.get("similarity", 0) > 0]
    check("L5 retrieval ceiling is BEHAVIOURALLY true, not just labelled: a query sharing literal "
          "tokens with the ingested text scores above zero, while a semantically equivalent Chinese "
          "query sharing no token scores nothing — the label and the measured behaviour agree",
          len(literal_hits) >= 1 and len(zh_hits) == 0,
          "literal_hits=%d zh_hits=%d" % (len(literal_hits), len(zh_hits)))

    # =====================================================================
    # 7. write side really lands on disk
    # =====================================================================
    lic_img = tmp / "l5_lic.png"
    Image.new("RGB", (40, 40), (250, 250, 250)).save(lic_img)
    la = post("/api/v1/assets", json={"file_path": str(lic_img)}).json()
    expect_event("asset.ingested")
    lresp = post("/api/v1/assets/%s/license" % la["id"],
                 json={"license_status": "commercial", "license_type": "CC-BY",
                       "commercial_use": True, "verified": True,
                       "warnings": ["attribution required"]}).json()
    expect_event("asset.license_set")
    disk_rec = None
    disk_file = None
    for p in DATA.rglob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("licenses"), dict) and la["id"] in d["licenses"]:
            disk_rec = d["licenses"][la["id"]]
            disk_file = str(p.relative_to(DATA))
    bad_enum = post("/api/v1/assets/%s/license" % la["id"], json={"license_status": "cc_by"})
    ghost_lic = post("/api/v1/assets/no-such-asset/license", json={"license_status": "free"})

    check("L5 write side: setting a licence status is really persisted — this test reads the JSON "
          "store back itself and finds the same status/commercial flag it wrote (the endpoint's own "
          "persisted flag is corroborated, not trusted), and invalid enums / ghost assets are honest "
          "4xx instead of silent no-ops",
          lresp.get("persisted") is True and disk_rec is not None
          and disk_rec.get("license_status") == "commercial"
          and disk_rec.get("commercial_use") is True
          and disk_rec.get("warnings") == ["attribution required"]
          and bad_enum.status_code == 400 and ghost_lic.status_code == 404,
          "persisted=%s disk=%s file=%s bad_enum=%d ghost=%d"
          % (lresp.get("persisted"), json.dumps({k: disk_rec.get(k) for k in
                                                 ("license_status", "commercial_use")}) if disk_rec else None,
             disk_file, bad_enum.status_code, ghost_lic.status_code))

    # =====================================================================
    # 8. no drift in the two self-describing registries
    # =====================================================================
    caps = get("/api/v1/capabilities").json()
    summ = caps.get("summary") or {}
    check("L5 zero-drift: the capability truth table is the Phase 78 one and no lineage work in "
          "this layer quietly moves it (43 items / 40 supported / 3 needs_adobe_host / "
          "0 prototype_not_shipped / 0 not_shipped) — the 39→41 / 34→36 delta is Phase 53's own "
          "declared red-line split (installer build-evidence verification + read-only install "
          "preflight shipped as real engines' capabilities), the 36→37 / 2→1 delta is Phase 56's "
          "(brand_semantic_recognition turned supported via real Provider vision passthrough), "
          "the 37→38 / 1→0 delta is Phase 58's (installer_execution turned supported by a REAL "
          "silent install + REAL bundled-uninstaller removal on this machine, receipts derived from "
          "disk+registry diff, executor reachable only through an explicit CLI consent token), and "
          "the 41→42 / 38→39 delta is Phase 77's (cdr_document_read added: CorelDRAW has no UXP "
          "runtime, so the same §8/§9 bridge pipe is reused with an attach-only COM host script; "
          "three read-only tools only), and the 42→43 / 39→40 delta is Phase 78's "
          "(cdr_document_write added: a minimal create/move write set with a server-side confirm "
          "gate plus host-side recheck, post-write readback, best-effort undo grouping, and a hard "
          "never-Save boundary; delete/rename/save/export are not registered). "
          "not_shipped is now empty by measurement, not by declaration: the only caps still not "
          "shipped are the three that need a live Adobe host process, which is a different column. "
          "Re-pinned from live /capabilities measurement, so any further drift trips this check",
          summ == {"total": 43, "supported": 40, "needs_adobe_host": 3,
                   "prototype_not_shipped": 0, "not_shipped": 0},
          json.dumps(summ))

    t0 = time.time()
    rep = post("/api/v1/eval/run", timeout=3000).json()
    es = rep.get("summary") or {}
    check("L5 zero-drift: the evaluation suite still runs every golden case green with calibration "
          "intact (honest=true means the deliberately-wrong calibration cases were caught, so the "
          "green score is falsifiable rather than decorative) — executed/passed re-pinned to this "
          "machine's live count after Phase 53 grew the installer_manifest golden domain 4→10 "
          "(111→117), Phase 56 added the brand_semantics golden domain 5 (117→122), Phase 57 "
          "grew the barcode golden domain 5→10 with the QR truth cases (122→127) and Phase 58 "
          "added the installer_execution golden domain 6 plus the R14 卸载撤销七臂 one case "
          "(127→133→134), and Phase 59 route B grew it again：runtime_guard 升独立黄金域 5 真值 "
          "(134→139)，负控校准 30→32（runtime_guard 1 枚 + preflight 新校准 1 枚）。中途两枚探针（p58_l2_refreeze_probe over HTTP、p58_fixture_count_probe "
          "in-process）把 exec 域冻在 5 枚 / 133 executed / 163 results——那时夹具还是假想的五臂形制；"
          "执行器夹具按产品真形重写为六枚之后，盘上真相 p58_eval_exec_payload.json（沙箱根见 "
          "p58_exec_disk_freeze.py）是 134 executed / 134 passed / 164 results / 30 calibration，"
          "同口径进程内复测见 p58_eval_duration_out.txt。so a case "
          "silently dropping out of the run trips this rather than the number "
          "being a decorative constant. Phase 63 (2026-10-11) grew the barcode golden domain 10->14 with "
          "four 1D-decode oracle truth cases (executed 139->143, calibration 32->34, suite "
          "171->177; every one of those six numbers re-derived live from this machine's own "
          "on-disk eval report, none transcribed)",
          es.get("executed_cases") == 143 and es.get("passed") == 143 and es.get("failed") == 0
          and rep.get("honest") is True and rep.get("calibration_error") is False
          and es.get("crashed_cases") == 0,
          "executed=%s passed=%s honest=%s calibration_error=%s seconds=%.1f report=%s"
          % (es.get("executed_cases"), es.get("passed"), rep.get("honest"),
             rep.get("calibration_error"), time.time() - t0, rep.get("report_id")))
    expect_event("eval.completed")

    # =====================================================================
    # 9. the event ledger mirrors the process exactly (graded last: the two
    # write-side/eval kinds above are the final triggers, so a snapshot taken
    # any earlier would report them as "never triggered")
    # =====================================================================
    ev_all = get("/api/v1/events/recent", params={"limit": 2000}).json()
    kc_all = ev_all.get("kind_counts") or {}
    missing = sorted(set(DECLARED_KINDS) - set(kc_all))
    phantom = sorted(set(kc_all) - set(DECLARED_KINDS))
    check("L5 ledger: every event kind declared by _emit() in the artifact under test really fired "
          "during this run and no kind fired that the source does not declare (the expected set is "
          "derived from agent_service.py at runtime, never hand-listed, so a silently-unreachable "
          "emitter shows up as a gap instead of being forgotten)",
          not missing and not phantom and len(DECLARED_KINDS) == 26,
          "declared=%d fired=%d missing=%s phantom=%s"
          % (len(DECLARED_KINDS), len(kc_all), missing, phantom))

    count_diff = {k: [triggered.get(k, 0), kc_all.get(k, 0)]
                  for k in set(triggered) | set(kc_all)
                  if triggered.get(k, 0) != kc_all.get(k, 0)}
    check("L5 ledger: each kind fired EXACTLY as many times as this test independently tallied, and "
          "the published total equals the sum of the per-kind counts — the ledger is a faithful mirror "
          "of the process, neither lossy (an emit that never reached a subscriber) nor padded (a "
          "double-emit behind a single endpoint call)",
          not count_diff and ev_all.get("published_total") == sum(kc_all.values())
          == sum(triggered.values()),
          "expected=%d published=%s mismatches=%s"
          % (sum(triggered.values()), ev_all.get("published_total"),
             json.dumps(count_diff, ensure_ascii=False)))

    # =====================================================================
    # 10. Phase 58 — 执行面的值血缘：一枚 sha256 走四个读者，中途不许换值
    # =====================================================================
    # 为什么这一族属于血缘层而不属于契约层：执行器唯一有意义的失败是"账上的值"与"盘上的值"
    # 分家——台账写 sha=A、制品实际是 sha=B，或对账时把台账重算成盘上的 B（那样就永远对得上，
    # 等于没有对账）。这四个读者互相独立：构建台账（写于编译那一轮）、执行台账（写于装机那一轮）、
    # 本测试自己 hashlib 算的、HTTP 观测面回显的。四个值逐字段相等才算这条链活着。
    import hashlib as _hl58
    import shutil as _sh58

    def _rj58(_r):
        return _r.json() if _r.status_code == 200 else {"__http__": _r.status_code}

    _p58 = DATA / "installer" / "_p58"
    _p58.mkdir(parents=True, exist_ok=True)
    _art58 = _p58 / "zaojing-ai-1.0.0-setup.exe"
    _art58b = _art58.read_bytes() if _art58.is_file() else b"MZ\x90\x00" + b"zaojing-p58-lineage" * 11
    _sha_art = _hl58.sha256(_art58b).hexdigest()
    _log58 = _p58 / "install.log"
    _log58b = ("[install] verifying archive\n[install] extracting payload\n"
               "[install] completed successfully\n").encode("utf-8")
    _sha_log = _hl58.sha256(_log58b).hexdigest()
    _pre58 = _p58 / "payload" / "config.json"
    _pre58.parent.mkdir(parents=True, exist_ok=True)
    _pre58b = b'{"provider":"none","theme":"glass"}'
    _pre58.write_bytes(_pre58b)
    _sha_pre = _hl58.sha256(_pre58b).hexdigest()

    def _led58(artifact_path, artifact_sha, install_dir):
        return {
            "schema": "zaojing-installer-exec-ledger/v1",
            "created_at": "2026-10-04T00:00:00", "updated_at": "2026-10-04T00:00:01",
            "install": {
                "status": "install_verified", "installed": True, "exit_code": 0,
                "argv": ["install-exec", "install", "--confirm", "***"],
                "process": {"pid": 0, "returncode": 0},
                "install_dir": install_dir,
                "artifact": {"path": artifact_path, "sha256": artifact_sha,
                             "bytes": len(_art58b)},
                "log": {"path": str(_log58), "sha256": _sha_log, "seen_finished_line": True},
                "checks_count": 3,
                "checks": [{"name": "files", "ok": True}, {"name": "registry", "ok": True},
                           {"name": "shortcut", "ok": True}],
                "diff": {"created_count": 1, "created": [{"path": artifact_path}]},
                "preexisting": {"files": [{"path": str(_pre58), "sha256": _sha_pre,
                                           "bytes": len(_pre58b), "rel": "config.json",
                                           "root": "data_dir"}]},
            },
            "uninstall": {
                "status": "uninstall_verified", "performed": True,
                "install_dir_gone": True, "uninstall_key_gone": True, "app_key_gone": True,
                "preexisting": {"count": 1, "intact": True, "deviating": [],
                                "files": [{"path": str(_pre58), "sha256": _sha_pre}]},
            },
        }

    _led58p = DATA / "installer" / "execution-evidence.json"
    _tgt58 = str(_p58 / "root-never-created")

    def _write58(payload_bytes):
        _led58p.write_bytes(payload_bytes)
        return payload_bytes

    _bl58 = {"artifact_path": str(_art58.resolve()), "artifact_bytes": len(_art58b),
             "artifact_sha256": _sha_art, "script_sha256": _sha_pre, "iscc_exit_code": 0,
             "warnings": []}
    (_p58 / "build-evidence.json").write_bytes(
        json.dumps(_bl58, ensure_ascii=False).encode("utf-8"))
    _art58.write_bytes(_art58b)
    _log58.write_bytes(_log58b)
    _led58raw = _write58(json.dumps(_led58(str(_art58), _sha_art, _tgt58),
                                    ensure_ascii=False).encode("utf-8"))
    _sha_led = _hl58.sha256(_led58raw).hexdigest()

    _g1 = _rj58(get("/api/v1/installer/execution"))
    check("L5 P58 lineage hop 1->4: ONE sha256 read by FOUR independent readers must not change "
          "value anywhere on the way — the build ledger's artifact_sha256, this test's own hashlib "
          "of the artifact bytes off disk, the executor's recomputed on-disk value, and the "
          "recorded value inside the execution ledger are all byte-equal; the ledger receipt the "
          "HTTP face echoes (bytes/sha256) equals this test hashing the very same file; the log "
          "sha survives the round-trip; the pre-existing-data baseline is really recorded AND "
          "really read back; basis names disk+registry and the seven self-report booleans are "
          "echoed-but-ignored",
          _g1.get("status") == "executed_verified" and _g1.get("mismatches") == []
          and _g1.get("fabricated") is False
          and _g1.get("basis") == "derived_from_disk_and_registry"
          and _g1.get("recomputed", {}).get("build_ledger_artifact_sha256") == _sha_art
          and _g1.get("recomputed", {}).get("artifact_sha256_on_disk_now") == _sha_art
          and _sha_art == _hl58.sha256(_art58.read_bytes()).hexdigest()
          and _g1.get("ledger", {}).get("sha256") == _sha_led
          and _g1.get("ledger", {}).get("bytes") == len(_led58raw)
          and _g1.get("recomputed", {}).get("log_sha256_on_disk_now") == _sha_log
          and _g1.get("preexisting", {}).get("recorded_in_ledger") is True
          and _g1.get("preexisting", {}).get("intact") is True
          and _g1.get("preexisting", {}).get("count") == 1
          and _g1.get("recomputed", {}).get("install_dir_now_exists") is False
          and len(_g1.get("self_report_ignored") or []) == 7
          and _g1.get("recorded_only") == ["install.exit_code", "install.argv", "install.process"],
          json.dumps({k: _g1.get(k) for k in ("status", "mismatches", "basis")},
                     ensure_ascii=False)[:400])

    _tampered = bytearray(_art58b)
    _tampered[-1] ^= 0x01
    _art58.write_bytes(bytes(_tampered))
    _g2 = _rj58(get("/api/v1/installer/execution"))
    _art58.write_bytes(_art58b)
    _g3 = _rj58(get("/api/v1/installer/execution"))
    _m2 = " ".join(_g2.get("mismatches") or [])
    check("L5 P58 one-byte tamper breaks the chain at the reader that owns it, and the chain is NOT "
          "recomputed to match disk: flipping the LAST byte of the artifact (same length, so the "
          "build ledger's bytes-gate still passes) must turn executed_verified into "
          "ledger_inconsistent naming install.artifact.sha256, the recomputed on-disk value must "
          "SHOW the new hash while the recorded build-ledger value keeps naming the old one (if "
          "both sides moved, no reconciliation would ever be able to fail), and restoring the byte "
          "returns the verdict to executed_verified (the observer is not sticky)",
          _g2.get("status") == "ledger_inconsistent"
          and "install.artifact.sha256" in _m2
          and _g2.get("recomputed", {}).get("artifact_sha256_on_disk_now")
              == _hl58.sha256(bytes(_tampered)).hexdigest()
          and _g2.get("recomputed", {}).get("build_ledger_artifact_sha256") == _sha_art
          and _g2.get("ledger", {}).get("sha256") == _sha_led
          and _g3.get("status") == "executed_verified" and _g3.get("mismatches") == [],
          json.dumps({"g2": _g2.get("mismatches"),
                      "g2_now": _g2.get("recomputed", {}).get("artifact_sha256_on_disk_now"),
                      "g3": _g3.get("status")}, ensure_ascii=False)[:400])

    _forged = _led58(str(_p58 / "artifact-that-is-not-there.exe"), "a" * 64, _tgt58)
    _forgedraw = _write58(json.dumps(_forged, ensure_ascii=False).encode("utf-8"))
    # 「观测面不许写盘」这条声称必须在**被观测的那次 GET 前后**取样才成立。
    # 首版把这两处取样都写晚了：ledger 字节比对放在下一条臂已经合法覆写台账之后
    # （p58_clause_diag 实测：11 臂只 arm#5 `_led58p.read_bytes() == _forgedraw` 红，
    # 而同一条 check 里 `_g4.ledger.sha256 == sha256(_forgedraw)` 是绿的——服务端读到的
    # 就是那份伪造台账，是判卷侧自己把文件换了），树差分则跨过了本层自己的 unlink/rmtree
    # （8 臂只 arm#5 `_tree_before == _tree_after` 红）。两处皆为判卷侧时序错，非产品缺陷。
    _treeA48 = {str(p.relative_to(DATA / "installer")): p.stat().st_size
                for p in (DATA / "installer").rglob("*") if p.is_file()}
    _g4 = _rj58(get("/api/v1/installer/execution"))
    _treeB48 = {str(p.relative_to(DATA / "installer")): p.stat().st_size
                for p in (DATA / "installer").rglob("*") if p.is_file()}
    _led_after_g4 = _led58p.read_bytes()
    _p58c = _p58 / "corrupt"
    _p58c.mkdir(parents=True, exist_ok=True)
    (_p58c / "build-evidence.json").write_bytes(b'{"artifact_sha256": ')
    _art58c = _p58c / "zaojing-ai-1.0.0-setup.exe"
    _art58c.write_bytes(_art58b)
    _g5raw = _led58(str(_art58c), _sha_art, _tgt58)
    _g5raw["install"]["preexisting"] = {"files": []}
    _write58(json.dumps(_g5raw, ensure_ascii=False).encode("utf-8"))
    _g5 = get("/api/v1/installer/execution")
    _g5j = _rj58(_g5)
    _m4 = " ".join(_g4.get("mismatches") or [])
    _m5 = " ".join(_g5j.get("mismatches") or [])
    check("L5 P58 self-report alone never verifies, and a read-only face must ALWAYS give a verdict: "
          "a ledger whose every claim says install_verified/uninstall_verified, installed=true, "
          "exit_code=0 with a sha that matches nothing, but whose artifact is simply not on disk, "
          "must come back ledger_inconsistent naming install.artifact.unreadable (never "
          "executed_verified, and the ledger's own bytes stay untouched by the observer); and when "
          "the build ledger next to a real artifact is un-parseable, the reconciliation must "
          "DEGRADE into a named mismatch with HTTP 200 — a 500 here would let the reader conclude "
          "'the reconcile logic is fine, it just crashed', while the truth is it refused to rule "
          "(same lesson as P53's unreadable-artifact arm, applied to the second reader)",
          _g4.get("status") == "ledger_inconsistent"
          and "install.artifact.unreadable" in _m4
          and "install.artifact.sha256" in _m4
          and _g4.get("recomputed", {}).get("artifact_sha256_on_disk_now", "") == ""
          and _g4.get("ledger", {}).get("sha256") == _hl58.sha256(_forgedraw).hexdigest()
          and _led_after_g4 == _forgedraw
          and _g5.status_code == 200
          and _g5j.get("__http__") is None
          and _g5j.get("status") == "ledger_inconsistent"
          and "build_ledger" in _m5
          and _g5j.get("fabricated") is False,
          json.dumps({"g4": _g4.get("mismatches"), "g5_http": _g5.status_code,
                      "g5": _g5j.get("mismatches"),
                      "g5_recomputed_bl": _g5j.get("recomputed", {}).get(
                          "build_ledger_artifact_sha256")}, ensure_ascii=False)[:400])

    # 本层自己的收摊（删台账、删夹具）必须发生在**取样之前**：首版把 `_tree_before` 取在
    # 删除之前，于是 `_tree_before == _tree_after` 在构造上就不可能成立（p58_clause_diag 实测
    # 8 臂只这一臂红，其余含 ledger_absent/无回执全绿）。判据的名字本来就写着"before and
    # after the last GET"，现在代码与名字对齐：取样贴着那次 GET 的前后。
    _led58p.unlink()
    _sh58.rmtree(_p58, ignore_errors=True)
    _tree_before = {str(p.relative_to(DATA / "installer")): p.stat().st_size
                    for p in (DATA / "installer").rglob("*") if p.is_file()}
    _g6 = _rj58(get("/api/v1/installer/execution"))
    _tree_after = {str(p.relative_to(DATA / "installer")): p.stat().st_size
                   for p in (DATA / "installer").rglob("*") if p.is_file()}
    check("L5 P58 observation is inert: six GETs on the reconciliation face changed nothing on disk "
          "— with the ledger removed the honest verdict is ledger_absent (with a named reason and "
          "no fabricated receipt), the install subtree this layer owns is byte-for-byte the same "
          "file list before and after the last GET, the same holds for the GET that faced a "
          "missing artifact (取样于该次 GET 前后，跨条臂自己覆写台账的时点不算), and no fixture "
          "file was recreated by the service itself",
          _g6.get("status") == "ledger_absent" and _g6.get("fabricated") is False
          and _g6.get("ledger", {}).get("exists") is False
          and bool(_g6.get("reason")) and _g6.get("mismatches") == []
          and _tree_before == _tree_after and _treeA48 == _treeB48
          and not _led58p.exists() and not _p58.exists(),
          json.dumps({"g6": _g6.get("status"), "delta": sorted(
              set(_tree_before) ^ set(_tree_after)), "delta_g4": sorted(
              set(_treeA48) ^ set(_treeB48))}, ensure_ascii=False)[:300])

    # route-hit counters: the new endpoint must have been really reached at runtime
    metrics_payload = get("/api/v1/metrics").json()
    rh = metrics_payload.get("route_hits") or {}
    check("L5 runtime evidence: the lineage endpoint was really hit at runtime (P44 route counter), "
          "and every route this layer exercised was hit EXACTLY as many times as this layer called it "
          "(route_hits keys are FastAPI route templates, so the ten graph executions share one key — "
          "four for the Phase 51 graph/bad-graph/index set, six added by the Phase 54 container-shape "
          "set: four shapes + ghost root + grammar; Phase 58 adds the reconciliation face: six GETs "
          "by this layer = baseline/one-byte-tamper/restore/missing-artifact/corrupt-build-ledger/"
          "absent — the counter is the proof that the six verdicts above came from six real requests "
          "and not from a cached or hand-written expectation)",
          rh.get("/api/v1/workflow/tools", 0) == 1
          and rh.get("/api/v1/workflow/define", 0) == 10
          and rh.get("/api/v1/workflow/{workflow_id}/execute", 0) == 10
          and rh.get("/api/v1/packaging/dieline", 0) == 1
          and rh.get("/api/v1/packaging/panel-topology", 0) == 4
          and rh.get("/api/v1/installer/execution", 0) == 6
          and rh.get("/api/v1/workflow/l5lineage/execute", 0) == 0,
          json.dumps({k: v for k, v in rh.items()
                      if "workflow" in k or "packaging" in k or "installer" in k},
                     ensure_ascii=False))

finally:
    try:
        proc.terminate()
        proc.wait(timeout=15)
    except Exception:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        except Exception:
            pass
    srv_log.close()

fails = [x for x in results if x[0] == "[FAIL]"]
print("=" * 60)
print("L5 TOTAL %d | PASS %d | FAIL %d" % (len(results), len(results) - len(fails), len(fails)))
print("L5 data root kept for inspection: %s" % tmp)
sys.exit(1 if fails else 0)
