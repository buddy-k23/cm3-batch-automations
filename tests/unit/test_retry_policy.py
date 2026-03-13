from src.services.retry_policy import execute_with_retries


def test_execute_with_retries_succeeds_after_retry():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    assert execute_with_retries(flaky, max_attempts=3, base_delay_seconds=0.0) == "ok"
    assert state["n"] == 2
