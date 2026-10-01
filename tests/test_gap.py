"""POST /gap 核心逻辑单元测试（纯 Python，不依赖 FastAPI）。"""

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


def _is_feasible_age(payload: dict, ages: dict[str, int]) -> bool:
    """见证是否满足全部区间与关系约束。"""
    for item in payload["strata"]:
        sid = item["id"]
        if not (item["youngest_ma"] <= ages[sid] <= item["oldest_ma"]):
            return False
    for rel in payload["relations"]:
        if ages[rel["older_id"]] < ages[rel["younger_id"]] + 1:
            return False
    return True


class GapBasicTests(unittest.TestCase):
    def test_chain_bounds(self):
        # a -> b -> c，全 [0,100]：a-b 至少 1；a 取 100、b 受 b>=c+1>=1
        # 限制取 1，最大为 99（独立区间端点相减会错给成 100）。
        result = gap(
            {
                "strata": [_s(x, 0, 100) for x in "abc"],
                "relations": [_r("a", "b"), _r("b", "c")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["a_id"], "a")
        self.assertEqual(result["b_id"], "b")
        self.assertEqual((result["min_gap"], result["max_gap"]), (1, 99))
        self.assertEqual(
            result["min_witness"]["a"] - result["min_witness"]["b"], 1
        )
        self.assertEqual(
            result["max_witness"]["a"] - result["max_witness"]["b"], 99
        )

    def test_witnesses_cover_all_strata_and_are_feasible(self):
        payload = {
            "strata": [_s(x, 0, 100) for x in "abcd"],
            "relations": [
                _r("a", "b"),
                _r("a", "c"),
                _r("b", "d"),
                _r("c", "d"),
            ],
            "a_id": "a",
            "b_id": "d",
        }
        result = gap(payload)
        self.assertEqual((result["min_gap"], result["max_gap"]), (2, 100))
        for key in ("min_witness", "max_witness"):
            witness = result[key]
            self.assertEqual(set(witness), {"a", "b", "c", "d"})
            self.assertTrue(_is_feasible_age(payload, witness))

    def test_same_stratum_query_is_zero(self):
        result = gap(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [_r("a", "b")],
                "a_id": "a",
                "b_id": "a",
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["min_gap"], 0)
        self.assertEqual(result["max_gap"], 0)
        self.assertEqual(
            result["min_witness"]["a"] - result["min_witness"]["a"], 0
        )

    def test_disconnected_strata_supported(self):
        # 两层互不连通、无任何关系：a-b 可在 [-10, 10] 间任意取整。
        result = gap(
            {
                "strata": [_s("a", 0, 10), _s("b", 0, 10)],
                "relations": [],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual((result["min_gap"], result["max_gap"]), (-10, 10))
        self.assertEqual(
            result["min_witness"]["a"] - result["min_witness"]["b"], -10
        )
        self.assertEqual(
            result["max_witness"]["a"] - result["max_witness"]["b"], 10
        )

    def test_disconnected_components_with_inner_relations(self):
        # 两条无关链 c->d 与 a->b，均在 [0,10]；a 与 d 跨组件。
        result = gap(
            {
                "strata": [_s(x, 0, 10) for x in "abcd"],
                "relations": [_r("c", "d"), _r("a", "b")],
                "a_id": "a",
                "b_id": "d",
            }
        )
        # a ∈ [1,10]（a 比 b 古老），d ∈ [0,9]（c 比 d 古老），
        # 两组件间自由组合：a-d 最小 1-9=-8，最大 10-0=10。
        self.assertEqual((result["min_gap"], result["max_gap"]), (-8, 10))

    def test_negative_and_large_intervals(self):
        result = gap(
            {
                "strata": [_s("a", -5, 5), _s("b", -5, 5)],
                "relations": [],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual((result["min_gap"], result["max_gap"]), (-10, 10))

        big = 10**12
        result = gap(
            {
                "strata": [_s("a", -big, big), _s("b", -big, big)],
                "relations": [_r("a", "b")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual((result["min_gap"], result["max_gap"]), (1, 2 * big))

    def test_chain_of_two_hundred_strata(self):
        n = 200
        ids = [f"s{i:03d}" for i in range(n)]
        result = gap(
            {
                "strata": [_s(sid, 0, 10**6) for sid in ids],
                "relations": [_r(ids[i], ids[i + 1]) for i in range(n - 1)],
                "a_id": ids[0],
                "b_id": ids[-1],
            }
        )
        self.assertEqual(result["status"], "gap")
        self.assertEqual((result["min_gap"], result["max_gap"]), (n - 1, 10**6))


class PassthroughTests(unittest.TestCase):
    def test_cycle_passthrough(self):
        result = gap(
            {
                "strata": [_s(x, 0, 100) for x in "abc"],
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
                "strata": [
                    _s("a", 50, 50),
                    _s("b", 0, 100),
                    _s("c", 50, 50),
                ],
                "relations": [_r("a", "b"), _r("b", "c")],
                "a_id": "a",
                "b_id": "b",
            }
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["conflicting_strata"], ["a", "b", "c"])
        self.assertEqual(
            result["tight_intervals"]["b"],
            {"youngest_ma": 51, "oldest_ma": 49},
        )


class GapValidationTests(unittest.TestCase):
    def _errors(self, payload: dict) -> list[str]:
        try:
            gap(payload)
        except AuditValidationError as exc:
            return exc.errors
        self.fail("应当抛出 AuditValidationError")

    def test_unknown_query_id_rejected(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "a_id": "a",
                "b_id": "ghost",
            }
        )
        self.assertTrue(any("未提交" in e and "ghost" in e for e in errors))

    def test_both_query_ids_unknown_reported(self):
        errors = self._errors(
            {
                "strata": [_s("a", 0, 10)],
                "relations": [],
                "a_id": "x",
                "b_id": "y",
            }
        )
        joined = "\n".join(errors)
        self.assertIn("x", joined)
        self.assertIn("y", joined)

    def test_audit_level_validation_still_applies(self):
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
    """小规模随机用例：边界与见证均与暴力枚举对照。"""

    def test_against_brute_force(self):
        rng = random.Random(20261001)
        for _ in range(120):
            n = rng.randint(1, 4)
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
            a_id, b_id = rng.choice(ids), rng.choice(ids)
            payload = {
                "strata": strata,
                "relations": relations,
                "a_id": a_id,
                "b_id": b_id,
            }
            expected = _brute_force(payload)
            result = gap(payload)
            if expected is None:
                self.assertEqual(result["status"], "conflict", payload)
                continue
            lo_gap, hi_gap = expected
            self.assertEqual(result["status"], "gap", payload)
            self.assertEqual(result["min_gap"], lo_gap, payload)
            self.assertEqual(result["max_gap"], hi_gap, payload)
            # 见证本身合法且确实达到所声称的边界。
            self.assertTrue(_is_feasible_age(payload, result["min_witness"]))
            self.assertTrue(_is_feasible_age(payload, result["max_witness"]))
            self.assertEqual(
                result["min_witness"][a_id] - result["min_witness"][b_id],
                lo_gap,
                payload,
            )
            self.assertEqual(
                result["max_witness"][a_id] - result["max_witness"][b_id],
                hi_gap,
                payload,
            )


def _brute_force(payload: dict) -> tuple[int, int] | None:
    """暴力枚举全部整数赋值，返回 ``(min(a-b), max(a-b))``；无解 None。"""
    strata = {item["id"]: item for item in payload["strata"]}
    ids = sorted(strata)
    domains = [
        range(strata[sid]["youngest_ma"], strata[sid]["oldest_ma"] + 1)
        for sid in ids
    ]
    lo_gap: int | None = None
    hi_gap: int | None = None
    for combo in itertools.product(*domains):
        age = dict(zip(ids, combo))
        if any(
            age[rel["older_id"]] < age[rel["younger_id"]] + 1
            for rel in payload["relations"]
        ):
            continue
        value = age[payload["a_id"]] - age[payload["b_id"]]
        lo_gap = value if lo_gap is None else min(lo_gap, value)
        hi_gap = value if hi_gap is None else max(hi_gap, value)
    if lo_gap is None:
        return None
    return lo_gap, hi_gap  # type: ignore[return-value]


if __name__ == "__main__":
    unittest.main()
