from pathlib import Path

from src.workflows.engine import build_validate_cmd, resolve_path


ROOT = Path(__file__).resolve().parents[2]


def test_resolve_path_handles_relative_and_absolute():
    rel = resolve_path("data/samples/customers.txt", ROOT)
    assert str(rel).endswith("data/samples/customers.txt")

    abs_in = str(ROOT / "data/samples/customers.txt")
    abs_out = resolve_path(abs_in, ROOT)
    assert str(abs_out) == abs_in


def test_build_validate_cmd_includes_strict_flags():
    cmd = build_validate_cmd(
        py="python",
        input_file="in.txt",
        mapping="m.json",
        detailed=False,
        strict_fixed_width=True,
        strict_level="format",
    )
    assert "--basic" in cmd
    assert "--strict-fixed-width" in cmd
    assert "--strict-level" in cmd
