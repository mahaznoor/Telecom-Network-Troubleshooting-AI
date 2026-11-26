"""Focused tests for the deterministic offline response evaluator."""

import copy
import unittest
from io import StringIO

from evaluation.evaluate import evaluate_case, load_cases, print_report, run_evaluation


class OfflineEvaluationTests(unittest.TestCase):
    def test_bundled_fixtures_pass_and_report_without_external_calls(self) -> None:
        report = run_evaluation()

        self.assertEqual(report["mode"], "offline_mock_fixtures")
        self.assertEqual(report["external_api_calls"], 0)
        self.assertEqual(report["total_cases"], 5)
        self.assertEqual(report["passed_cases"], 5)
        self.assertEqual(report["failed_cases"], 0)

        output = StringIO()
        print_report(report, output)
        self.assertIn("Total evaluation cases: 5", output.getvalue())
        self.assertIn("Passed cases: 5", output.getvalue())
        self.assertIn("Failed cases: 0", output.getvalue())

    def test_unsupported_command_fails_context_grounding_checks(self) -> None:
        case = copy.deepcopy(
            next(
                item
                for item in load_cases()
                if item["id"] == "insufficient-command-context"
            )
        )
        case["mock_response"] += "\nRun `factory-reset` now."

        result = evaluate_case(case)

        self.assertFalse(result["passed"])
        self.assertIn("commands_supported_by_context", result["failures"])
        self.assertIn("unsupported_claims_absent", result["failures"])

    def test_citation_to_unretrieved_page_fails(self) -> None:
        case = copy.deepcopy(
            next(item for item in load_cases() if item["id"] == "citation-required")
        )
        case["mock_response"] = case["mock_response"].replace(
            "Page: 5", "Page: 99"
        )

        result = evaluate_case(case)

        self.assertFalse(result["passed"])
        self.assertIn("citations_grounded_in_context", result["failures"])
        self.assertIn("required_citations_present", result["failures"])


if __name__ == "__main__":
    unittest.main()
