from src.services.compare_job_store import CompareJobStore


def test_compare_job_store_persists_status_and_result(tmp_path):
    store = CompareJobStore(db_path=tmp_path / "compare_jobs.db")

    store.create("job-1", status="queued")
    row = store.get("job-1")
    assert row is not None
    assert row["status"] == "queued"

    store.update("job-1", status="running")
    row = store.get("job-1")
    assert row is not None
    assert row["status"] == "running"

    payload = {"matching_rows": 10, "differences": 1}
    store.update("job-1", status="completed", result=payload)
    row = store.get("job-1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["result"] == payload


def test_compare_job_store_returns_none_for_missing_job(tmp_path):
    store = CompareJobStore(db_path=tmp_path / "compare_jobs.db")
    assert store.get("missing") is None
