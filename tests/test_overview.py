import unittest

from app.metrics import overview
from app.runner import settings_from


def record(**values):
    return {"settings": settings_from({"target": "host", "mode": "bidir", "duration": 30}),
            "intervals": [], "summary": {}, **values}


class OverviewTests(unittest.TestCase):
    def test_peaks_use_aggregate_intervals_not_average_or_streams(self):
        result = overview(record(intervals=[
            {"sum": {"end": 1, "bits_per_second": 80e6},
             "sum_bidir_reverse": {"end": 1, "bits_per_second": 120e6},
             "streams": [{"end": 1, "bits_per_second": 40e6}]},
            {"sum": {"end": 2, "bits_per_second": 100e6},
             "sum_bidir_reverse": {"end": 2, "bits_per_second": 90e6}},
            {"sum": {"end": 3, "bits_per_second": 999e6, "omitted": True}}],
            summary={"sum_received": {"bits_per_second": 60e6, "end": 2.04}}))
        self.assertEqual(result["max_upload_mbps"], 100)
        self.assertEqual(result["max_download_mbps"], 120)
        self.assertEqual(result["runtime_seconds"], 2.04)
        self.assertEqual(len(result["trend"]), 4)

    def test_cancelled_runtime_is_measured_not_configured_or_wall_time(self):
        result = overview(record(status="cancelled", created_at="2026-10-10T10:00:00Z",
                                 finished_at="2026-10-10T10:00:20Z",
                                 intervals=[{"sum": {"end": 2.7, "bits_per_second": 10e6}}]))
        self.assertEqual(result["runtime_seconds"], 2.7)
        self.assertEqual(result["max_upload_mbps"], 10)
        self.assertNotIn("max_download_mbps", result)

    def test_missing_intervals_do_not_turn_average_into_peak(self):
        result = overview(record(summary={"sum_received": {"seconds": 5, "bits_per_second": 40e6}}))
        self.assertEqual(result, {"runtime_seconds": 5, "trend": []})
        self.assertEqual(overview(record(status="failed")), {"runtime_seconds": None, "trend": []})

    def test_long_trends_preserve_extrema_and_endpoints(self):
        intervals = [{"sum": {"end": i, "bits_per_second": (999 if i == 141 else i % 10) * 1e6}}
                     for i in range(1, 1001)]
        result = overview(record(intervals=intervals))
        self.assertLessEqual(len(result["trend"]), 60)
        self.assertEqual(result["trend"][0]["seconds"], 1)
        self.assertEqual(result["trend"][-1]["seconds"], 1000)
        self.assertEqual(max(point["mbps"] for point in result["trend"]), 999)
        self.assertEqual(result["max_upload_mbps"], 999)
        self.assertEqual(result["runtime_seconds"], 1000)

    def test_text_parallel_streams_are_aggregated_and_runtime_survives(self):
        settings = settings_from({"target": "host"})
        result = overview(record(settings=settings, output_format="text", intervals=[
            {"stream": "4", "end": 1, "bits_per_second": 10e6},
            {"stream": "5", "end": 1, "bits_per_second": 20e6}],
            summary={"text_summaries": {"receiver": [{"stream": "SUM", "end": 1.03, "bits_per_second": 28e6}]}}))
        self.assertEqual(result["max_upload_mbps"], 30)
        self.assertEqual(result["runtime_seconds"], 1.03)
        self.assertEqual(len(result["trend"]), 1)

    def test_zero_measurements_are_valid_but_nonfinite_samples_are_not(self):
        result = overview(record(intervals=[{"sum": {"end": 1, "bits_per_second": 0}},
                                             {"sum": {"end": 2, "bits_per_second": float("nan")}}]))
        self.assertEqual(result["max_upload_mbps"], 0)
        self.assertEqual(result["runtime_seconds"], 1)
        self.assertEqual(len(result["trend"]), 1)


if __name__ == "__main__":
    unittest.main()
