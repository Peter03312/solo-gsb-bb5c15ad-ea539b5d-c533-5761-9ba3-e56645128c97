"""两层年代差极值：在审计约束下求 ``age(a_id) − age(b_id)`` 的最小/最大值。

与 /audit 共用同一套约束（整数年代区间 + ``age(older) >= age(younger) + 1``），
另收两个查询编号 ``a_id``、``b_id``，在全体合法整数赋值上计算

    gap = age(a_id) − age(b_id)

的最小值与最大值，并各返回一个达到该边界的全体地层年代映射（任意合法
见证即可）。

算法（差分约束，不逐年代枚举）
------------------------------
关系图（无环）边权取 1 即 ``age(u) − age(v) >= 1``，区间给出单层约束
``L0[v] <= age(v) <= U0[v]``。先用与 /audit 相同的 DAG 双向传播得到
收紧后的边际可行区间 ``[L, U]``。系统可行时，差分约束的标准结论给出：

* 令 ``d(x, y)`` 为关系 DAG 上 ``x -> y`` 的最长路长度（边权 1，
  不可达记为 −∞），它正是约束传播出的强制下界
  ``age(y) − age(x) >= d(x, y)``；

* 最大值（``gap = age(a) − age(b)``）由两类上界取最小：
  单层区间给的 ``U[a] − L[b]``，以及当 ``b`` 可达 ``a`` 时关系链强制的
  ``age(a) − age(b) <= −d(b, a)``（``a`` 必须比 ``b`` 年轻的情形）；

* 最小值对称取最大：``L[a] − U[b]`` 与可达时的 ``d(a, b)``。

即::

    gmin = max(L[a] − U[b], d(a,b))          # 不可达时第二项忽略
    gmax = min(U[a] − L[b], −d(b,a))         # 不可达时第二项忽略

**不能**直接相减各层独立可行区间的端点：``L[a] − U[b]`` / ``U[a] − L[b]``
只覆盖区间这一类约束，关系链给出的跨层强制差（``d`` 项）必须同时参与，
否则在“有向关系迫使查询点反向排序”等情形会得到根本取不到的值。

边界见证（可行赋值）的构造同样不枚举：把源点钉在其传播下界
（``age(s) = L[s]``，该取值总可被某个全局合法赋值实现），其余层先取各自
上界再沿拓扑正向收紧 ``U(v) = min(U(v), U(u) − 1)``——此赋值满足全部
区间与关系约束，且使目标差取到对应极值：钉 ``a`` 得最小差见证，
钉 ``b`` 得最大差见证。

同层查询（``a_id == b_id``）两个极值均为 0，见证即任意可行赋值；
互不连通的地层无 ``d`` 项，由各自区间端点决定极值，同样支持。
"""

from __future__ import annotations

from typing import Any

from .audit import (
    AuditValidationError,
    find_cycle,
    lexicographic_topo,
    tighten_bounds,
    validate_and_build,
)

#: DAG 最长路中“不可达”的标记；约束跨度 ≤ 层数（≤ 200），该值足够小。
_NEG_INF = -(10**18)


def gap(data: dict[str, Any]) -> dict[str, Any]:
    """计算年代差极值，返回可直接序列化的结果字典。

    结果形态与 /fit 对齐：原约束成环/压空时原样返回 cycle/conflict；
    否则返回 ``{"status": "gap", "a_id", "b_id", "min_gap", "max_gap",
    "min_gap_ages", "max_gap_ages"}``。
    """
    raw_strata: list[dict[str, Any]] = list(data.get("strata") or [])
    raw_relations: list[dict[str, Any]] = list(data.get("relations") or [])
    a_id: str = data.get("a_id")
    b_id: str = data.get("b_id")

    # ---- 1. 基本校验（与 /audit 完全一致的约束语义） --------------------
    ids, bounds, adjacency = validate_and_build(raw_strata, raw_relations)
    _validate_query(ids, a_id, b_id)

    # ---- 2. 原约束无解时沿用现有 cycle/conflict 结果 --------------------
    cycle = find_cycle(ids, adjacency)
    if cycle is not None:
        return {"status": "cycle", "cycle_path": cycle}

    topo = lexicographic_topo(ids, adjacency)
    lower = {sid: bounds[sid][0] for sid in ids}
    upper = {sid: bounds[sid][1] for sid in ids}
    tighten_bounds(topo, adjacency, lower, upper)

    conflicts = sorted(sid for sid in ids if lower[sid] > upper[sid])
    if conflicts:
        return {
            "status": "conflict",
            "conflicting_strata": conflicts,
            "tight_intervals": {
                sid: {
                    "youngest_ma": lower[sid],
                    "oldest_ma": upper[sid],
                }
                for sid in ids
            },
        }

    # ---- 3. 差分约束求 age(a) − age(b) 的最小/最大值 --------------------
    dist_a = _longest_distances(a_id, topo, adjacency)
    dist_b = _longest_distances(b_id, topo, adjacency)

    # 关系链强制的跨层差：a 比 b 古老至少 d(a,b)；b 比 a 古老则强制
    # age(a) − age(b) <= −d(b,a)。不可达时该项不参与取极。
    min_gap = lower[a_id] - upper[b_id]
    if dist_a[b_id] != _NEG_INF:
        forced = dist_a[b_id]
        if forced > min_gap:
            min_gap = forced

    max_gap = upper[a_id] - lower[b_id]
    if dist_b[a_id] != _NEG_INF:
        forced = -dist_b[a_id]
        if forced < max_gap:
            max_gap = forced

    # ---- 4. 各取一个达到边界的全体地层年代映射 --------------------------
    return {
        "status": "gap",
        "a_id": a_id,
        "b_id": b_id,
        "min_gap": min_gap,
        "max_gap": max_gap,
        "min_gap_ages": _boundary_witness(
            a_id, ids, topo, adjacency, lower, upper
        ),
        "max_gap_ages": _boundary_witness(
            b_id, ids, topo, adjacency, lower, upper
        ),
    }


def _validate_query(ids: list[str], a_id: Any, b_id: Any) -> None:
    """校验查询编号：必须各引用一个已提交的地层，否则抛 422。"""
    known = set(ids)
    errors: list[str] = []
    for field, value in (("a_id", a_id), ("b_id", b_id)):
        if not isinstance(value, str) or not value:
            errors.append(f"查询编号 {field} 必须是非空字符串")
        elif value not in known:
            errors.append(f"查询编号 {field} 引用了未提交的地层编号：{value!r}")
    if errors:
        raise AuditValidationError(errors)


def _longest_distances(
    source: str,
    topo: list[str],
    adjacency: dict[str, set[str]],
) -> dict[str, int]:
    """关系 DAG 上 ``source`` 到各层的最长路长度（边权 1）。

    按拓扑序一遍 DP：``dist[v] = max(dist[v], dist[u] + 1)``，
    不可达记为 :data:`_NEG_INF`；``dist[source] = 0``。
    """
    dist = {sid: _NEG_INF for sid in topo}
    dist[source] = 0
    for u in topo:
        base = dist[u]
        if base == _NEG_INF:
            continue
        candidate = base + 1
        for v in adjacency[u]:
            if candidate > dist[v]:
                dist[v] = candidate
    return dist


def _boundary_witness(
    pin: str,
    ids: list[str],
    topo: list[str],
    adjacency: dict[str, set[str]],
    lower: dict[str, int],
    upper: dict[str, int],
) -> dict[str, int]:
    """构造一个把 ``pin`` 钉在其传播下界 ``L[pin]`` 的可行年代映射。

    其余层先取各自收紧上界，再沿拓扑正向传播 ``U(v) = min(U(v), U(u)-1)``。
    系统可行时 ``L[pin]`` 必可被某全局赋值取到（差分约束最短/最长路
    见证性质），故传播后不会与任何下界冲突，得到的点值构成合法赋值。

    令 ``pin = a_id`` 时该赋值取到 ``age(a) − age(b)`` 的最小值；
    令 ``pin = b_id`` 时取到最大值（同层查询二者相同）。
    """
    ages_lower = dict(lower)
    ages_upper = dict(upper)
    ages_lower[pin] = ages_upper[pin] = lower[pin]
    tighten_bounds(topo, adjacency, ages_lower, ages_upper)
    return {sid: ages_upper[sid] for sid in ids}
