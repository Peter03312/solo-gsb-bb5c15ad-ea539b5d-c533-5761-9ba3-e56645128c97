"""POST /gap 核心求解逻辑单元测试（纯 Python，不依赖 FastAPI）。"""

from __future__ import annotations

import itertools
import random
import unittest

from app.audit import AuditValidationError
from app.gap import gap


def _s(sid: str, youngest: int, oldest: int) -> dict:
    return {"id": sid, "youngest_ma": youngest, "oldest_ma": oldest}


def _r(older: str, younger: str) -> dict:
    return {"older_id": older, "younger_id": younger}


class GapBasicTests(unittest.TestCase):
    def test_independent_strata_use_interval_endpoints(self):
        # 互不连通：a ∈ [0,10]，b ∈ [0,10]，age(a)−age(b) ∈ [−10, 10]。
        result = gap(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], -10)
        self.assertEqual(result["max_gap"], 10)
        self.assertEqual(result["min_gap_ages"], {"a": 0, "b": 10})
        self.assertEqual(result["max_gap_ages"], {"a": 10, "b": 0})

    def test_same_id_returns_zero(self):
        result = gap(
            {
                "strata": [_s("a", 3, 7), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "a_id": "a",
                "b_id": "a",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], 0)
        self.assertEqual(result["max_gap"], 0)
        ages = result["min_gap_ages"]
        self.assertEqual(ages["a"] - ages["a"], 0)
        self.assertEqual(result["max_gap_ages"]["a"], ages["a"])

    def test_chain_forces_lower_gap(self):
        # a -> b -> c，均 [0,100]。收紧后 a∈[2,100], b∈[1,99], c∈[0,98]。
        result = gap(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
                "relations": [_r("a", "b"), _r("b", "c")],
                "a_id": "a",
                "b_id": "c",
            }
        )
        self.assertEqual(result["status"], "gap")
        # 关系链强制 age(a) − age(c) >= 2；区间给 [2−98, 100−0]。
        self.assertEqual(result["min_gap"], 2)
        self.assertEqual(result["max_gap"], 100)

    def test_reverse_relation_caps_max_gap(self):
        # b -> a（b 比 a 古老），均 [0,10]：age(a) − age(b) <= −1。
        result = gap(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("b", "a")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], -10)
        self.assertEqual(result["max_gap"], -1)
        ages = result["max_gap_ages"]
        self.assertEqual(ages["a"] - ages["b"], -1)

    def test_disconnected_components_supported(self):
        result = gap(
            {
                "strata": [
                    _s("a", 0, 5),
                    _s("b", 20, 25),
                    _s("c", 100, 100),
                ],
                "relations": [],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], 0 - 25)
        self.assertEqual(result["max_gap"], 5 - 20)
        # 见证覆盖全部地层，含与查询无关的 c。
        self.assertEqual(result["min_gap_ages"]["c"], 100)
        self.assertEqual(result["max_gap_ages"]["c"], 100)

    def test_witnesses_satisfy_all_constraints(self):
        result = gap(
            {
                "strata": [_s(s, 0, 20) for s in ("a", "b", "c", "d")],
                "relations": [
                    _r("a", "b"),
                    _r("a", "c"),
                    _r("b", "d"),
                    _r("c", "d"),
                ],
                "a_id": "d",
                "b_id": "a",
            }
        )
        self.assertEqual(result["status"], "gap")
        for key in ("min_gap_ages", "max_gap_ages"):
            ages = result[key]
            self.assertEqual(set(ages), {"a", "b", "c", "d"})
            self.assertGreaterEqual(ages["a"], ages["b"] + 1)
            self.assertGreaterEqual(ages["a"], ages["c"] + 1)
            self.assertGreaterEqual(ages["b"], ages["d"] + 1)
            self.assertGreaterEqual(ages["c"], ages["d"] + 1)
        self.assertEqual(
            result["min_gap_ages"]["d"] - result["min_gap_ages"]["a"],
            result["min_gap"],
        )
        self.assertEqual(
            result["max_gap_ages"]["d"] - result["max_gap_ages"]["a"],
            result["max_gap"],
        )

    def test_point_intervals(self):
        result = gap(
            {
                "strata": [_s("a", 7, 7), _s("b", 3, 3)],
                "relations": [],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], 4)
        self.assertEqual(result["max_gap"], 4)
        self.assertEqual(result["min_gap_ages"], {"a": 7, "b": 3})

    def test_negative_ages(self):
        # a ∈ [-10,-5]，b ∈ [-8,-3]，a 比 b 古老：收紧为 a∈[-7,-5]、b∈[-8,-6]。
        result = gap(
            {
                "strata": [_s("a", -10, -5), _s("b", -8, -3)],
                "relations": [_r("a", "b")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], 1)  # 关系强制至少相差 1
        self.assertEqual(result["max_gap"], 3)


class PassthroughTests(unittest.TestCase):
    def test_cycle_passthrough(self):
        result = gap(
            {
                "strata": [_s(s, 0, 100) for s in ("a", "b", "c")],
                "relations": [_r("a", "b"), _r("b", "c"), _r("c", "a")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "cycle")
        path = result["cycle_path"]
        self.assertEqual(path[0], path[-1])
        self.assertEqual(set(result.keys()), {"status", "cycle_path"})

    def test_conflict_passthrough(self):
        result = gap(
            {
                "strata": [_s("a", 5, 5), _s("b", 5, 5)],
                "relations": [_r("a", "b")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicting_strata"], ["a", "b"])


class QueryValidationTests(unittest.TestCase):
    def _errors(self, data: dict) -> list[str]:
        try:
            gap(data)
        except AuditValidationError as exc:
            return exc.errors
        self.fail("应当抛出 AuditValidationError")

    def test_unknown_a_id(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "a_id": "x",
                "b_id": "b",
            }
        )
        self.assertTrue(any("a_id" in e and "未提交" in e for e in errors))

    def test_unknown_b_id(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "a_id": "a",
                "b_id": "y",
            }
        )
        self.assertTrue(any("b_id" in e and "未提交" in e for e in errors))

    def test_both_unknown_collected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "a_id": "x",
                "b_id": "y",
            }
        )
        self.assertEqual(len(errors), 2)

    def test_audit_validation_still_applies(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [_r("a", "a")],
                "a_id": "a",
                "b_id": "a",
            }
        )
        self.assertTrue(any("自身古老" in e for e in errors))


class BruteForceCrossCheckTests(unittest.TestCase):
    """小规模随机用例与暴力枚举对照（极值 + 边界见证合法性）。"""

    def test_against_brute_force(self):
        rng = random.Random(20260926)
        for _ in range(300):
            n = rng.randint(1, 5)
            ids = [f"s{i}" for i in range(n)]
            strata = []
            for sid in ids:
                lo = rng.randint(-4, 4)
                hi = lo + rng.randint(0, 4)
                strata.append(_s(sid, lo, hi))
            order = ids[:]
            rng.shuffle(order)
            relations = []
            for i in range(n):
                for j in range(i + 1, n):
                    if rng.random() < 0.4:
                        relations.append(_r(order[i], order[j]))
            a = rng.choice(ids)
            b = rng.choice(ids)
            payload = {
                "strata": strata,
                "relations": relations,
                "a_id": a,
                "b_id": b,
            }
            expected = _brute_force(payload)
            result = gap(payload)
            if expected is None:
                self.assertEqual(result["status"], "conflict", payload)
                continue
            emin, emax = expected
            self.assertEqual(result["status"], "gap", payload)
            self.assertEqual(result["min_gap"], emin, payload)
            self.assertEqual(result["max_gap"], emax, payload)
            for key, bound in (
                ("min_gap_ages", result["min_gap"]),
                ("max_gap_ages", result["max_gap"]),
            ):
                ages = result[key]
                self.assertEqual(set(ages), set(ids), payload)
                for sid in ids:
                    item = next(x for x in strata if x["id"] == sid)
                    self.assertGreaterEqual(ages[sid], item["youngest_ma"])
                    self.assertLessEqual(ages[sid], item["oldest_ma"])
                for rel in relations:
                    self.assertGreaterEqual(
                        ages[rel["older_id"]],
                        ages[rel["younger_id"]] + 1,
                        payload,
                    )
                self.assertEqual(ages[a] - ages[b], bound, payload)


def _brute_force(payload: dict) -> tuple[int, int] | None:
    """暴力枚举全部整数年代，返回 (最小差, 最大差)；无解返回 None。"""
    strata = {item["id"]: item for item in payload["strata"]}
    ids = sorted(strata)
    domains = [
        range(strata[sid]["youngest_ma"], strata[sid]["oldest_ma"] + 1)
        for sid in ids
    ]
    a, b = payload["a_id"], payload["b_id"]
    ai, bi = ids.index(a), ids.index(b)
    values: list[int] = []
    for combo in itertools.product(*domains):
        age = dict(zip(ids, combo))
        if any(
            age[rel["older_id"]] < age[rel["younger_id"]] + 1
            for rel in payload["relations"]
        ):
            continue
        values.append(combo[ai] - combo[bi])
    if not values:
        return None
    return min(values), max(values)


if __name__ == "__main__":
    unittest.main()
