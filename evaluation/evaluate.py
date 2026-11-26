"""Run deterministic, offline checks against the response evaluation fixtures."""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple


SECTIONS = (
    "Issue Summary",
    "Possible Causes",
    "Step-by-Step Troubleshooting",
    "Recommended Solution",
    "Verification Steps",
    "Sources / References",
)
_CITATION_PATTERN = re.compile(r"\[Source:\s*([^,\]\n]+),\s*Page:\s*(\d+)\]")
_LIMITATION_PHRASES = (
    "not enough information",
    "does not specify",
    "documentation does not",
    "cannot recommend",
    "can't recommend",
    "insufficient documentation",
)


def load_cases(dataset_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load offline response fixtures without initializing application services."""
    path = Path(dataset_path) if dataset_path else Path(__file__).with_name(
        "structured_response_cases.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["evaluation_cases"]
    if not isinstance(cases, list):
        raise ValueError("evaluation_cases must be a JSON list")
    return cases


def _citation_tuples(text: str) -> List[Tuple[str, int]]:
    return [(source.strip(), int(page)) for source, page in _CITATION_PATTERN.findall(text)]


def evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate one fixed response fixture using deterministic text checks."""
    answer = case["mock_response"]
    context = case.get("context", "")
    answer_lower = answer.lower()
    context_lower = context.lower()
    checks: Dict[str, bool] = {}

    if case.get("expects_structured_response", False):
        section_matches = [
            list(re.finditer(rf"(?m)^##\s+{re.escape(section)}\s*$", answer))
            for section in SECTIONS
        ]
        positions = [matches[0].start() for matches in section_matches if matches]
        checks["all_six_sections_once_and_in_order"] = (
            all(len(matches) == 1 for matches in section_matches)
            and positions == sorted(positions)
        )
    else:
        has_section_heading = any(
            re.search(rf"(?m)^##\s+{re.escape(section)}\s*$", answer)
            for section in SECTIONS
        )
        checks["general_response_not_forced_into_sections"] = not has_section_heading

    actual_citations = _citation_tuples(answer)
    context_citations = set(_citation_tuples(context))
    expected_citations = {
        (item["source_file"], int(item["page"]))
        for item in case.get("expected_citations", [])
    }
    citation_marker_count = answer.count("[Source:")
    checks["citation_format_valid"] = citation_marker_count == len(actual_citations)
    checks["citations_grounded_in_context"] = (
        set(actual_citations).issubset(context_citations)
        and set(actual_citations).issubset(expected_citations)
    )
    if case.get("requires_citation", False):
        checks["required_citations_present"] = expected_citations.issubset(
            set(actual_citations)
        )
    else:
        checks["required_citations_present"] = not actual_citations

    required_phrases = case.get("required_phrases", [])
    checks["supported_details_present"] = all(
        phrase.lower() in answer_lower and phrase.lower() in context_lower
        for phrase in required_phrases
    )

    forbidden_phrases = case.get("forbidden_phrases", [])
    checks["unsupported_claims_absent"] = all(
        phrase.lower() not in answer_lower for phrase in forbidden_phrases
    )

    command_candidates = case.get("command_candidates", [])
    unsupported_commands = [
        command
        for command in command_candidates
        if command.lower() in answer_lower and command.lower() not in context_lower
    ]
    checks["commands_supported_by_context"] = not unsupported_commands

    required_commands = case.get("required_commands", [])
    checks["required_commands_present_and_supported"] = all(
        command.lower() in answer_lower and command.lower() in context_lower
        for command in required_commands
    )

    claim_candidates = case.get("technical_claim_candidates", [])
    unsupported_claims = [
        claim
        for claim in claim_candidates
        if claim.lower() in answer_lower and claim.lower() not in context_lower
    ]
    checks["technical_claims_supported_by_context"] = not unsupported_claims

    if case.get("expects_insufficient_context", False):
        checks["insufficient_context_disclosed"] = any(
            phrase in answer_lower for phrase in _LIMITATION_PHRASES
        )

    if case.get("response_kind") == "general":
        checks["general_response_concise"] = len(answer.split()) <= case.get(
            "maximum_word_count", 40
        )

    failures = [name for name, passed in checks.items() if not passed]
    return {
        "case_id": case["id"],
        "scenario": case["scenario"],
        "passed": not failures,
        "checks": checks,
        "failures": failures,
    }


def run_evaluation(dataset_path: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate the bundled mock responses; no model, API, or database is used."""
    results = [evaluate_case(case) for case in load_cases(dataset_path)]
    passed = sum(result["passed"] for result in results)
    return {
        "mode": "offline_mock_fixtures",
        "external_api_calls": 0,
        "total_cases": len(results),
        "passed_cases": passed,
        "failed_cases": len(results) - passed,
        "results": results,
    }


def print_report(report: Dict[str, Any], stream: TextIO = sys.stdout) -> None:
    """Print a concise summary and per-case check results."""
    print("Offline Response Evaluation", file=stream)
    print("External API calls: 0", file=stream)
    print(f"Total evaluation cases: {report['total_cases']}", file=stream)
    print(f"Passed cases: {report['passed_cases']}", file=stream)
    print(f"Failed cases: {report['failed_cases']}", file=stream)
    print("Individual results:", file=stream)
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  [{status}] {result['case_id']}: {result['scenario']}", file=stream)
        for failure in result["failures"]:
            print(f"    Failed check: {failure}", file=stream)


def main() -> int:
    report = run_evaluation()
    print_report(report)
    return 1 if report["failed_cases"] else 0


if __name__ == "__main__":
    sys.exit(main())
