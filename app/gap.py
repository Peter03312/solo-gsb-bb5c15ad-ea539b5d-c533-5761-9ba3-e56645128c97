"""两点年代差边界：在 /audit 约束下求 ``age(a_id) - age(b_id)`` 的最小/最大值。

与 /audit、/fit 共用同一套整数差分约束::

    L_v <= age(v) <= U_v
    age(u) >= age(v) + 1          # 关系 u -> v（u 比 v 古老）

改写为标准差分约束 ``age(v) - age(u) <= w``（图上加入权为 w 的有向边
``u -> v``）：

* 关系 ``u -> v``：``age(v) - age(u) <= -1``；
* 上界 ``age(v) <= U_v``：引入恒取 0 的超级源点 0，边 ``0 -> v`` 权 ``U_v``；
* 下界 ``age(v) >= L_v``：边 ``v -> 0`` 权 ``-L_v``。

在可行（无负环）的差分约束系统中，对任意两点 ``a, b``：

    min(age(a) - age(b)) == -dist(a, b)
    max(age(a) - age(b)) ==  dist(b, a)

其中 ``dist`` 为该带权图上的最短路（双向连通由超级源点的边保证）。
因此只需两次 Bellman-Ford，既不逐年代枚举，也**不**直接相减各层独立
可行区间的端点——后者在两层互不连通或经由图中长路径间接约束时会给出
错误边界，而最短路会自动计入 ``age(u) >= age(v) + 1`` 的全部链式后果。

达到边界的全体地层年代映射取为最短路位势：以源点 ``s`` 的距离向量
``p_v = dist(s, v)`` 令 ``age(v) = p_v - p_0``，则全部差分约束取紧、
区间约束保持成立，且 ``age(a) - age(b)`` 恰好等于对应边界：
以 ``a`` 为源给出 ``-dist(a, b)``（最小），以 ``b`` 为源给出
``dist(b, a)``（最大）。
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

#: 超级源点编号（age(0) 恒为 0），各地层节点编号为 1..n。
_SUPER = 0


def gap(data: dict[str, Any]) -> dict[str, Any]:
    """计算两点年代差的最小/最大值，返回可直接序列化的结果字典。

    结果形态与 /audit、/fit 对齐：原约束成环/压空时原样返回
    cycle/conflict；否则返回 ``{"status": "gap", ...}``，附最小/最大
    差值及分别达到边界的全体地层年代映射（任一合法见证）。
    """
    raw_strata: list[dict[str, Any]] = list(data.get("strata") or [])
    raw_relations: list[dict[str, Any]] = list(data.get("relations") or [])
    a_id: str = data["a_id"]
    b_id: str = data["b_id"]

    # ---- 1. 基本校验（与 /audit 完全一致的约束语义） --------------------
    ids, bounds, adjacency = validate_and_build(raw_strata, raw_relations)

    # 查询编号必须已提交；a_id/b_id 相同是合法的“同层查询”。
    unknown = [sid for sid in (a_id, b_id) if sid not in bounds]
    if unknown:
        raise AuditValidationError(
            [
                "查询编号引用了未提交的地层编号："
                + ", ".join(repr(sid) for sid in unknown)
            ]
        )

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

    # ---- 3. 建差分约束图并两次最短路求边界 ------------------------------
    index = {sid: i + 1 for i, sid in enumerate(ids)}
    edges = _build_edges(ids, index, adjacency, lower, upper)
    n_nodes = len(ids) + 1

    if a_id == b_id:
        # 同层查询恒为零；见证取超级源点位势给出的一个全体可行赋值。
        dist = _bellman_ford(n_nodes, edges, _SUPER)
        witness = _witness(ids, index, dist)
        return {
            "status": "gap",
            "a_id": a_id,
            "b_id": b_id,
            "min_gap": 0,
            "max_gap": 0,
            "min_witness": witness,
            "max_witness": dict(witness),
        }

    # min(age(a) - age(b)) = -dist(a, b)，以 a 为源的位势即最小边界见证。
    dist_from_a = _bellman_ford(n_nodes, edges, index[a_id])
    min_gap = dist_from_a[index[a_id]] - dist_from_a[index[b_id]]
    min_witness = _witness(ids, index, dist_from_a)

    # max(age(a) - age(b)) = dist(b, a)，以 b 为源的位势即最大边界见证。
    dist_from_b = _bellman_ford(n_nodes, edges, index[b_id])
    max_gap = dist_from_b[index[a_id]] - dist_from_b[index[b_id]]
    max_witness = _witness(ids, index, dist_from_b)

    return {
        "status": "gap",
        "a_id": a_id,
        "b_id": b_id,
        "min_gap": min_gap,
        "max_gap": max_gap,
        "min_witness": min_witness,
        "max_witness": max_witness,
    }


def _build_edges(
    ids: list[str],
    index: dict[str, int],
    adjacency: dict[str, set[str]],
    lower: dict[str, int],
    upper: dict[str, int],
) -> list[tuple[int, int, int]]:
    """构造差分约束边 ``(u, v, w)``：``age(v) - age(u) <= w``。"""
    edges: list[tuple[int, int, int]] = []
    for sid in ids:
        node = index[sid]
        edges.append((_SUPER, node, upper[sid]))   # age(v) <= U_v
        edges.append((node, _SUPER, -lower[sid]))  # age(v) >= L_v
    for u in ids:
        for v in adjacency[u]:
            edges.append((index[u], index[v], -1))  # age(v) <= age(u) - 1
    return edges


def _bellman_ford(
    n_nodes: int, edges: list[tuple[int, int, int]], source: int
) -> list[int]:
    """带负权边、无负环图上的 Bellman-Ford 最短路。

    调用前约束系统已经 /audit 的成环与区间压空检查，故不存在负环；
    且每个地层节点都有与超级源点之间的双向边，图中所有节点皆可达，
    距离必有限。最多迭代 ``n_nodes - 1`` 轮，中途无更新即提前结束。
    """
    infinity = 10**30
    dist = [infinity] * n_nodes
    dist[source] = 0
    for _ in range(n_nodes - 1):
        changed = False
        for u, v, weight in edges:
            candidate = dist[u] + weight
            if candidate < dist[v]:
                dist[v] = candidate
                changed = True
        if not changed:
            break
    return dist


def _witness(
    ids: list[str], index: dict[str, int], dist: list[int]
) -> dict[str, int]:
    """把最短路位势平移为满足原始区间的全体地层年代映射。"""
    offset = dist[_SUPER]
    return {sid: dist[index[sid]] - offset for sid in ids}
