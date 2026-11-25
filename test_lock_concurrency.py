import asyncio


class MockRetriever:
    def __init__(self):
        self.k = 5
        self.similarity_threshold = 0.7
        self.use_mmr = True


class MockRAGChain:
    def __init__(self, delay=0.1):
        self.delay = delay
        self.call_count = 0

    async def ainvoke(self, question, chat_history):
        self.call_count += 1
        await asyncio.sleep(self.delay)
        return {"answer": "test", "context": []}


async def test_concurrent_race_condition():
    retriever = MockRetriever()
    rag_chain = MockRAGChain(delay=0.1)
    lock = asyncio.Lock()
    results = []
    errors = []

    async def make_request(req_k, req_threshold, req_mmr, should_fail=False):
        async with lock:
            try:
                old_k = retriever.k
                old_threshold = retriever.similarity_threshold
                old_mmr = retriever.use_mmr

                if req_k:
                    retriever.k = req_k
                if req_threshold is not None:
                    retriever.similarity_threshold = req_threshold
                if req_mmr is not None:
                    retriever.use_mmr = req_mmr

                await rag_chain.ainvoke("q", "")

                if should_fail:
                    raise ValueError("simulated failure")

                results.append({
                    "obs_k": retriever.k,
                    "obs_thr": retriever.similarity_threshold,
                    "obs_mmr": retriever.use_mmr,
                    "exp_k": req_k,
                    "exp_thr": req_threshold,
                    "exp_mmr": req_mmr,
                })
                return "success"
            except Exception as e:
                errors.append(str(e))
                return "error"
            finally:
                retriever.k = old_k
                retriever.similarity_threshold = old_threshold
                retriever.use_mmr = old_mmr

    # Run two concurrent requests with DIFFERENT settings
    task_a = make_request(3, 0.5, False)
    task_b = make_request(10, 0.9, True)

    result_a, result_b = await asyncio.gather(task_a, task_b)

    print("Request A result:", result_a)
    print("Request B result:", result_b)
    print()
    print("Results (observed during invoke):")
    for r in results:
        print(
            "  k={} (expected {}), threshold={} (expected {}), mmr={} (expected {})".format(
                r["obs_k"], r["exp_k"],
                r["obs_thr"], r["exp_thr"],
                r["obs_mmr"], r["exp_mmr"],
            )
        )
    print()
    print("Errors:", errors)
    print()

    # Verify settings are restored after both requests
    print("Retriever state AFTER both requests:")
    print("  k={} (should be 5)".format(retriever.k))
    print("  similarity_threshold={} (should be 0.7)".format(retriever.similarity_threshold))
    print("  use_mmr={} (should be True)".format(retriever.use_mmr))
    print()

    # Assertions
    assert retriever.k == 5, "k not restored: {}".format(retriever.k)
    assert retriever.similarity_threshold == 0.7, "threshold not restored: {}".format(retriever.similarity_threshold)
    assert retriever.use_mmr is True, "mmr not restored: {}".format(retriever.use_mmr)
    assert len(results) == 2, "Expected 2 results, got {}".format(len(results))
    assert len(errors) == 0, "Unexpected errors: {}".format(errors)

    # Verify each request saw its own settings during invoke (no race)
    for r in results:
        assert r["obs_k"] == r["exp_k"], "Race on k: observed {}, expected {}".format(r["obs_k"], r["exp_k"])
        assert r["obs_thr"] == r["exp_thr"], "Race on threshold"
        assert r["obs_mmr"] == r["exp_mmr"], "Race on mmr"

    print("ALL CONCURRENCY TESTS PASSED")


async def test_settings_restored_after_failure():
    retriever = MockRetriever()
    rag_chain = MockRAGChain(delay=0.05)
    lock = asyncio.Lock()

    async def make_failing_request():
        async with lock:
            try:
                old_k = retriever.k
                old_threshold = retriever.similarity_threshold
                old_mmr = retriever.use_mmr

                retriever.k = 999
                retriever.similarity_threshold = 0.01
                retriever.use_mmr = False

                await rag_chain.ainvoke("q", "")
                raise ValueError("simulated failure")
            except ValueError:
                pass
            finally:
                retriever.k = old_k
                retriever.similarity_threshold = old_threshold
                retriever.use_mmr = old_mmr

    await make_failing_request()

    print("After failed request:")
    print("  k={} (should be 5)".format(retriever.k))
    print("  similarity_threshold={} (should be 0.7)".format(retriever.similarity_threshold))
    print("  use_mmr={} (should be True)".format(retriever.use_mmr))

    assert retriever.k == 5, "k not restored after failure: {}".format(retriever.k)
    assert retriever.similarity_threshold == 0.7, "threshold not restored after failure"
    assert retriever.use_mmr is True, "mmr not restored after failure"

    print("FAILURE RESTORATION TEST PASSED")


if __name__ == "__main__":
    asyncio.run(test_concurrent_race_condition())
    print()
    asyncio.run(test_settings_restored_after_failure())
