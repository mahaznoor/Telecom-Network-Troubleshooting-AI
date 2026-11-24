"""Evaluation script for Telecom Network Troubleshooting AI."""

import json
import asyncio
from typing import Dict, Any, List
from backend.services.chat_service import ChatService
from backend.config import settings


class Evaluator:
    """Evaluate the quality of RAG answers."""

    def __init__(self):
        self.chat_service = ChatService()

    async def evaluate_question(self, question_data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a single test question."""
        question = question_data["question"]
        expected_keywords = question_data.get("expected_keywords", [])

        # Get AI answer
        result = await self.chat_service.process_question(
            question=question,
            session_id=f"eval_{question_data['id']}"
        )

        answer = result["answer"].lower()

        # Check keyword coverage
        found_keywords = [kw for kw in expected_keywords if kw.lower() in answer]
        missing_keywords = [kw for kw in expected_keywords if kw.lower() not in answer]

        coverage = len(found_keywords) / len(expected_keywords) if expected_keywords else 1.0

        return {
            "question_id": question_data["id"],
            "question": question,
            "category": question_data.get("category", "unknown"),
            "difficulty": question_data.get("difficulty", "unknown"),
            "answer": result["answer"],
            "sources_count": len(result["sources"]),
            "expected_keywords": expected_keywords,
            "found_keywords": found_keywords,
            "missing_keywords": missing_keywords,
            "keyword_coverage": coverage,
        }

    async def run_evaluation(self, questions_file: str = None) -> Dict[str, Any]:
        """Run full evaluation suite."""
        questions_file = questions_file or settings.EVAL_QUESTIONS_FILE

        with open(questions_file, 'r') as f:
            data = json.load(f)

        questions = data.get("test_questions", [])

        print(f"Evaluating {len(questions)} questions...")

        results = []
        for q in questions:
            print(f"  Evaluating {q['id']}: {q['question'][:50]}...")
            result = await self.evaluate_question(q)
            results.append(result)

        # Calculate summary stats
        total_coverage = sum(r["keyword_coverage"] for r in results)
        avg_coverage = total_coverage / len(results) if results else 0

        by_category = {}
        for r in results:
            cat = r["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(r["keyword_coverage"])

        category_stats = {
            cat: sum(scores) / len(scores)
            for cat, scores in by_category.items()
        }

        by_difficulty = {}
        for r in results:
            diff = r["difficulty"]
            if diff not in by_difficulty:
                by_difficulty[diff] = []
            by_difficulty[diff].append(r["keyword_coverage"])

        difficulty_stats = {
            diff: sum(scores) / len(scores)
            for diff, scores in by_difficulty.items()
        }

        summary = {
            "total_questions": len(results),
            "average_keyword_coverage": avg_coverage,
            "category_coverage": category_stats,
            "difficulty_coverage": difficulty_stats,
            "results": results,
        }

        return summary


async def main():
    """Main evaluation entry point."""
    evaluator = Evaluator()
    results = await evaluator.run_evaluation()

    # Print summary
    print("\n" + "="*50)
    print("EVALUATION SUMMARY")
    print("="*50)
    print(f"Total Questions: {results['total_questions']}")
    print(f"Average Keyword Coverage: {results['average_keyword_coverage']:.2%}")
    print("\nBy Category:")
    for cat, cov in results['category_coverage'].items():
        print(f"  {cat}: {cov:.2%}")
    print("\nBy Difficulty:")
    for diff, cov in results['difficulty_coverage'].items():
        print(f"  {diff}: {cov:.2%}")

    # Save detailed results
    output_file = "evaluation/results/evaluation_results.json"
    import os
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nDetailed results saved to {output_file}")


if __name__ == "__main__":
    asyncio.run(main())