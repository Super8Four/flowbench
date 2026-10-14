import copy
import unittest

from app.metrics import check_comparison, check_comparison_eligible, comparison, effective_settings, live_comparison, measurements, overview, series
try:
    from .check_evidence import check_record
except ImportError:
    from check_evidence import check_record


class CheckComparisonTests(unittest.TestCase):
    def test_timeout_to_response_is_useful_without_claiming_removed_answers(self):
        before = check_record(outcome="timeout")
        after = check_record(run_id="b" * 32)
        result = check_comparison(before, after)
        self.assertEqual(result["before"]["status"], "failed")
        self.assertIsNone(result["answer_sets"]["before"])
        self.assertIsNone(result["answer_sets"]["removed"])
        self.assertIsNone(result["answer_sets"]["added"])
        self.assertEqual(result["outcomes"]["before"]["timeout"], 1)
        self.assertTrue(result["comparable"])
        self.assertTrue(all(row["direction"] in {"changed", "unchanged"} for row in result["rows"]))

    def test_answer_deltas_ignore_ttl_but_preserve_outcome_meaning(self):
        before = check_record()
        after = copy.deepcopy(before); after["id"] = "b" * 32
        after["result"]["attempts"][0]["answers"][0]["ttl"] = 0
        self.assertEqual(check_comparison(before, after)["answer_sets"]["added"], [])
        after["result"]["attempts"][0]["answers"][0]["rdata"] = "203.0.113.8"
        result = check_comparison(before, after)
        self.assertEqual(result["answer_sets"]["removed"][0]["rdata"], "203.0.113.7")
        self.assertEqual(result["answer_sets"]["added"][0]["rdata"], "203.0.113.8")
        negative = check_comparison(check_record(outcome="nxdomain"), check_record(run_id="c" * 32, outcome="nodata"))
        self.assertEqual(negative["answer_sets"]["before"], [])
        self.assertEqual(negative["outcomes"]["before"]["nxdomain"], 1)
        self.assertEqual(negative["outcomes"]["after"]["nodata"], 1)

    def test_icmp_unknown_denominator_and_upper_bound_remain_null(self):
        before = check_record("loss", outcome="no_reply")
        after = check_record("loss", run_id="b" * 32, outcome="malformed")
        rows = {row["metric"]: row for row in check_comparison(before, after)["rows"]}
        self.assertEqual(rows["nonresponse_percent"]["before"], 100)
        self.assertIsNone(rows["nonresponse_percent"]["after"])
        self.assertIsNone(rows["nonresponse_percent"]["delta"])
        bounded = check_record("loss", run_id="c" * 32, outcome="upper_bound")
        rows = {row["metric"]: row for row in check_comparison(check_record("loss", outcome="reply"), bounded)["rows"]}
        self.assertEqual(rows["rtt_mean_ms"]["before"], 0)
        self.assertIsNone(rows["rtt_mean_ms"]["after"])
        self.assertEqual(bounded["result"]["attempts"][0]["rtt_upper_bound_ms"], 1)

    def test_conditions_and_terminal_eligibility_are_explicit(self):
        before = check_record(); after = check_record(run_id="b" * 32, count=2)
        result = check_comparison(before, after)
        self.assertFalse(result["comparable"])
        self.assertIn("count", result["settings_differences"])
        for nested, root in (("cancelled", "cancelled"), ("interrupted", "interrupted"), ("partial", "partial")):
            value = copy.deepcopy(before); value["result"]["status"] = nested; value["status"] = root
            self.assertTrue(check_comparison_eligible(value))
        after["status"] = "running"; after["result"]["status"] = "running"
        self.assertFalse(check_comparison_eligible(after))
        for value in (after, check_record("loss", run_id="b" * 32), before):
            with self.assertRaises(ValueError):
                check_comparison(before, value)

    def test_checks_never_acquire_throughput_even_with_poisoned_derived_fields(self):
        value = check_record()
        value.update(metrics={"upload_mbps": 999}, series=[{"mbps": 999}], summary={"sum_received": {"bits_per_second": 1e9}})
        self.assertEqual(measurements(value), {})
        self.assertEqual(series(value), [])
        self.assertEqual(effective_settings(value), value["settings"])
        self.assertEqual(overview(value)["trend"], [])
        for function in (comparison, live_comparison):
            with self.assertRaisesRegex(ValueError, "diagnostic|Diagnostic"):
                function(value, check_record(run_id="b" * 32))
