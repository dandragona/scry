"""Unit tests for scry-eval's DRACO parsing/scoring helpers:
parse_criterion_verdict, parse_oneshot, draco_aggregate, load_draco.

Pure-function tests (no subprocesses, no model CLIs) plus load_draco against
temp .jsonl fixtures written locally.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _harness as h  # noqa: E402


class TestParseCriterionVerdict(unittest.TestCase):
    def setUp(self):
        self.ev = h.load_scry_eval()

    def test_plain_json_met(self):
        self.assertEqual(
            self.ev.parse_criterion_verdict('{"criterion_status":"MET"}'), "MET")

    def test_plain_json_unmet(self):
        self.assertEqual(
            self.ev.parse_criterion_verdict('{"criterion_status":"UNMET"}'), "UNMET")

    def test_code_fenced_json(self):
        fenced = '```json\n{"criterion_status": "MET"}\n```'
        self.assertEqual(self.ev.parse_criterion_verdict(fenced), "MET")

    def test_code_fenced_json_bare_fence(self):
        fenced = '```\n{"criterion_status": "UNMET"}\n```'
        self.assertEqual(self.ev.parse_criterion_verdict(fenced), "UNMET")

    def test_lowercase_status_uppercased(self):
        # JSON path upper()s the value before returning.
        self.assertEqual(
            self.ev.parse_criterion_verdict('{"criterion_status":"met"}'), "MET")

    def test_loose_regex_fallback_unmet(self):
        # Not valid JSON, but the regex catches `criterion_status: UNMET`.
        text = "Here is my judgment, criterion_status: UNMET because reasons."
        self.assertEqual(self.ev.parse_criterion_verdict(text), "UNMET")

    def test_loose_regex_fallback_met_equals(self):
        text = "criterion_status = MET"
        self.assertEqual(self.ev.parse_criterion_verdict(text), "MET")

    def test_bare_text_returns_last_token(self):
        # Both tokens present; findall returns them in order, and the function
        # returns toks[-1] (NOT the first / NOT preferring UNMET).
        text = "At first I thought MET, but on reflection it is UNMET."
        self.assertEqual(self.ev.parse_criterion_verdict(text), "UNMET")

    def test_bare_text_last_token_met(self):
        text = "It started UNMET but is now MET."
        self.assertEqual(self.ev.parse_criterion_verdict(text), "MET")

    def test_bare_single_token(self):
        self.assertEqual(self.ev.parse_criterion_verdict("UNMET"), "UNMET")
        self.assertEqual(self.ev.parse_criterion_verdict("MET"), "MET")

    def test_empty_string_returns_none(self):
        self.assertIsNone(self.ev.parse_criterion_verdict(""))

    def test_none_returns_none(self):
        self.assertIsNone(self.ev.parse_criterion_verdict(None))

    def test_no_verdict_token_returns_none(self):
        self.assertIsNone(
            self.ev.parse_criterion_verdict("no decision could be reached here"))


class TestParseOneshot(unittest.TestCase):
    def setUp(self):
        self.ev = h.load_scry_eval()

    def test_basic_two_criteria(self):
        text = ('{"criteria_evaluations":['
                '{"criterion_idx":0,"criterion_status":"MET"},'
                '{"criterion_idx":1,"criterion_status":"UNMET"}]}')
        self.assertEqual(self.ev.parse_oneshot(text), {0: "MET", 1: "UNMET"})

    def test_code_fenced(self):
        text = ('```json\n{"criteria_evaluations":['
                '{"criterion_idx":0,"criterion_status":"MET"}]}\n```')
        self.assertEqual(self.ev.parse_oneshot(text), {0: "MET"})

    def test_prose_wrapped_brace_slice(self):
        # Surround the JSON with prose; the find('{')/rfind('}') slice recovers it.
        text = ('Sure, here is my evaluation:\n'
                '{"criteria_evaluations":[{"criterion_idx":2,"criterion_status":"unmet"}]}\n'
                'Let me know if you need more.')
        self.assertEqual(self.ev.parse_oneshot(text), {2: "UNMET"})

    def test_status_uppercased(self):
        text = ('{"criteria_evaluations":['
                '{"criterion_idx":0,"criterion_status":"met"}]}')
        self.assertEqual(self.ev.parse_oneshot(text), {0: "MET"})

    def test_missing_returns_none(self):
        self.assertIsNone(self.ev.parse_oneshot(""))
        self.assertIsNone(self.ev.parse_oneshot(None))

    def test_garbage_returns_none(self):
        self.assertIsNone(self.ev.parse_oneshot("this is not json at all"))

    def test_wrong_shape_returns_none(self):
        # Valid JSON dict but no criteria_evaluations list.
        self.assertIsNone(self.ev.parse_oneshot('{"foo": "bar"}'))
        # criteria_evaluations present but not a list.
        self.assertIsNone(self.ev.parse_oneshot('{"criteria_evaluations": "MET"}'))

    def test_non_int_idx_skipped(self):
        text = ('{"criteria_evaluations":['
                '{"criterion_idx":"0","criterion_status":"MET"},'
                '{"criterion_idx":1,"criterion_status":"UNMET"}]}')
        # The string idx entry is skipped; only the int idx survives.
        self.assertEqual(self.ev.parse_oneshot(text), {1: "UNMET"})

    def test_bad_status_skipped(self):
        text = ('{"criteria_evaluations":['
                '{"criterion_idx":0,"criterion_status":"MAYBE"},'
                '{"criterion_idx":1,"criterion_status":"MET"}]}')
        self.assertEqual(self.ev.parse_oneshot(text), {1: "MET"})

    def test_all_entries_bad_returns_empty_dict(self):
        # Well-formed shape but every entry skipped -> empty dict (not None).
        text = ('{"criteria_evaluations":['
                '{"criterion_idx":"x","criterion_status":"MET"}]}')
        self.assertEqual(self.ev.parse_oneshot(text), {})


class TestDracoAggregate(unittest.TestCase):
    def setUp(self):
        self.ev = h.load_scry_eval()

    def test_all_positive_half_met(self):
        r = self.ev.draco_aggregate([("MET", 1.0), ("UNMET", 1.0)])
        self.assertEqual(r["score"], 50.0)   # wsum(1.0) / pos(2.0) * 100
        self.assertEqual(r["graded"], 2)
        self.assertEqual(r["met"], 1)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["ungraded"], 0)
        self.assertEqual(r["raw"], 1.0)

    def test_none_counts_as_not_met_and_ungraded(self):
        r = self.ev.draco_aggregate([("MET", 1.0), (None, 1.0)])
        self.assertEqual(r["score"], 50.0)   # only the MET contributes to wsum
        self.assertEqual(r["graded"], 1)
        self.assertEqual(r["ungraded"], 1)
        self.assertEqual(r["met"], 1)
        self.assertEqual(r["total"], 2)

    def test_all_negative_no_error_full_score(self):
        # UNMET on a negative-weight criterion => error NOT present => perfect.
        r = self.ev.draco_aggregate([("UNMET", -1.0)])
        self.assertEqual(r["score"], 100.0)  # 1.0 + wsum(0)/neg(1) -> 1.0
        self.assertEqual(r["raw"], 0.0)
        self.assertEqual(r["met"], 0)
        self.assertEqual(r["graded"], 1)

    def test_all_negative_error_present_zero_score(self):
        # MET on a negative-weight criterion => error IS present => 0.
        r = self.ev.draco_aggregate([("MET", -1.0)])
        self.assertEqual(r["score"], 0.0)    # 1.0 + wsum(-1)/neg(1) -> 0.0
        self.assertEqual(r["raw"], -1.0)
        self.assertEqual(r["met"], 1)
        self.assertEqual(r["graded"], 1)

    def test_empty_reports_zero_score(self):
        r = self.ev.draco_aggregate([])
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["graded"], 0)
        self.assertEqual(r["ungraded"], 0)
        self.assertEqual(r["met"], 0)
        self.assertEqual(r["total"], 0)

    def test_zero_weight_only_zero_score(self):
        # pos == neg == 0 -> the else branch -> score 0.0.
        r = self.ev.draco_aggregate([("MET", 0.0), ("UNMET", 0.0)])
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["met"], 1)

    def test_score_clamps_to_100(self):
        # All positive criteria MET -> wsum == pos -> exactly 100, never above.
        r = self.ev.draco_aggregate([("MET", 2.0), ("MET", 3.0)])
        self.assertEqual(r["score"], 100.0)
        self.assertEqual(r["raw"], 5.0)

    def test_score_clamps_to_0(self):
        # All positive criteria UNMET -> wsum 0 -> exactly 0.
        r = self.ev.draco_aggregate([("UNMET", 1.0), ("UNMET", 2.0)])
        self.assertEqual(r["score"], 0.0)


class TestLoadDraco(unittest.TestCase):
    def setUp(self):
        self.ev = h.load_scry_eval()
        self.tmpdir = tempfile.mkdtemp(prefix="scry-draco-")

    def _write_jsonl(self, rows):
        path = os.path.join(self.tmpdir, "tasks.jsonl")
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return path

    @staticmethod
    def _answer(sections):
        return {"sections": sections}

    def test_flatten_criteria_with_section_id(self):
        ans = {"sections": [
            {"id": "s1", "criteria": [
                {"weight": 1.0, "requirement": "r1"},
                {"weight": -2.0, "requirement": "r2"}]},
            {"id": "s2", "criteria": [
                {"weight": 1.0, "requirement": "r3"}]}]}
        path = self._write_jsonl([
            {"id": "t1", "domain": "math", "problem": "p1", "answer": ans}])
        items = self.ev.load_draco(path)
        self.assertEqual(len(items), 1)
        crit = items[0]["criteria"]
        self.assertEqual(len(crit), 3)
        self.assertEqual([c["requirement"] for c in crit], ["r1", "r2", "r3"])
        self.assertEqual([c["section"] for c in crit], ["s1", "s1", "s2"])
        # weights coerced to float.
        self.assertEqual([c["weight"] for c in crit], [1.0, -2.0, 1.0])
        self.assertTrue(all(isinstance(c["weight"], float) for c in crit))
        self.assertEqual(items[0]["id"], "t1")
        self.assertEqual(items[0]["domain"], "math")
        self.assertEqual(items[0]["problem"], "p1")

    def test_answer_as_json_string_is_parsed(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": "r1"}]}])
        # answer stored as a JSON STRING (not a dict).
        path = self._write_jsonl([
            {"id": "t1", "domain": "d", "problem": "p", "answer": json.dumps(ans)}])
        items = self.ev.load_draco(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]["criteria"]), 1)
        self.assertEqual(items[0]["criteria"][0]["requirement"], "r1")

    def test_n_tasks_limits(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": "r"}]}])
        rows = [{"id": f"t{i}", "domain": f"d{i}", "problem": "p", "answer": ans}
                for i in range(3)]
        path = self._write_jsonl(rows)
        items = self.ev.load_draco(path, n_tasks=2)
        self.assertEqual(len(items), 2)
        self.assertEqual([it["id"] for it in items], ["t0", "t1"])

    def test_criteria_limit_caps_per_task(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": f"r{i}"} for i in range(5)]}])
        path = self._write_jsonl([
            {"id": "t1", "domain": "d", "problem": "p", "answer": ans}])
        items = self.ev.load_draco(path, criteria_limit=2)
        self.assertEqual(len(items[0]["criteria"]), 2)
        self.assertEqual([c["requirement"] for c in items[0]["criteria"]],
                         ["r0", "r1"])

    def test_stratified_collapses_same_domain(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": "r"}]}])
        rows = [
            {"id": "t1", "domain": "math", "problem": "p1", "answer": ans},
            {"id": "t2", "domain": "math", "problem": "p2", "answer": ans}]
        path = self._write_jsonl(rows)
        items = self.ev.load_draco(path, stratified=True)
        # Same domain -> only the first task kept.
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "t1")

    def test_stratified_keeps_distinct_domains(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": "r"}]}])
        rows = [
            {"id": "t1", "domain": "math", "problem": "p1", "answer": ans},
            {"id": "t2", "domain": "code", "problem": "p2", "answer": ans}]
        path = self._write_jsonl(rows)
        items = self.ev.load_draco(path, stratified=True)
        self.assertEqual(len(items), 2)
        self.assertEqual({it["id"] for it in items}, {"t1", "t2"})

    def test_blank_lines_ignored(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": "r"}]}])
        path = os.path.join(self.tmpdir, "blanks.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(
                {"id": "t1", "domain": "d", "problem": "p", "answer": ans}) + "\n")
            f.write("\n")
            f.write("   \n")
            f.write(json.dumps(
                {"id": "t2", "domain": "d2", "problem": "p", "answer": ans}) + "\n")
        items = self.ev.load_draco(path)
        self.assertEqual(len(items), 2)

    def test_missing_domain_defaults_empty(self):
        ans = self._answer([{"id": "s1", "criteria": [
            {"weight": 1.0, "requirement": "r"}]}])
        # No "domain" key -> defaults to "".
        path = self._write_jsonl([
            {"id": "t1", "problem": "p", "answer": ans}])
        items = self.ev.load_draco(path)
        self.assertEqual(items[0]["domain"], "")


if __name__ == "__main__":
    unittest.main()
