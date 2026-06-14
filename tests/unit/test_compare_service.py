from pathlib import Path

from src.services.compare_service import run_compare_service


ROOT = Path(__file__).resolve().parents[2]


def test_compare_service_non_chunked_header_fallback():
    result = run_compare_service(
        file1=str(ROOT / "data/samples/customers.txt"),
        file2=str(ROOT / "data/samples/customers_updated.txt"),
        keys="customer_id",
        mapping=None,
        detailed=False,
        use_chunked=False,
    )
    assert result["total_rows_file1"] == 10
    assert result["total_rows_file2"] == 10


def test_compare_service_chunked_keyed():
    result = run_compare_service(
        file1=str(ROOT / "data/samples/customers.txt"),
        file2=str(ROOT / "data/samples/customers_updated.txt"),
        keys="customer_id",
        detailed=False,
        use_chunked=True,
        chunk_size=1000,
        progress=False,
    )
    assert result["matching_rows"] >= 0
