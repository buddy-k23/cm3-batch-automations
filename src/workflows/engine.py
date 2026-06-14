from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any


def resolve_path(path_value: str | None, project_root: Path) -> Path | None:
    if not path_value:
        return None
    p = Path(path_value)
    return p if p.is_absolute() else project_root / p


def run_subprocess(cmd: list[str], cwd: Path) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    duration = round(time.perf_counter() - started, 3)
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return {
        "command": " ".join(cmd),
        "exit_code": proc.returncode,
        "duration_seconds": duration,
        "output": out.strip(),
        "status": "PASS" if proc.returncode == 0 else "FAIL",
    }


def build_parse_cmd(
    py: str,
    input_file: str,
    mapping: str,
    output: str | None = None,
    fmt: str | None = None,
    use_chunked: bool = False,
    chunk_size: int = 100000,
) -> list[str]:
    cmd = [py, "-m", "src.main", "parse", "-f", input_file, "-m", mapping]
    if output:
        cmd.extend(["-o", output])
    if fmt:
        cmd.extend(["--format", fmt])
    if use_chunked:
        cmd.extend(["--use-chunked", "--chunk-size", str(chunk_size)])
    return cmd


def build_validate_cmd(
    py: str,
    input_file: str,
    mapping: str,
    rules: str | None = None,
    output: str | None = None,
    detailed: bool = True,
    strict_fixed_width: bool = False,
    strict_level: str | None = None,
    use_chunked: bool = False,
    chunk_size: int = 100000,
    progress: bool = False,
) -> list[str]:
    cmd = [py, "-m", "src.main", "validate", "-f", input_file, "-m", mapping]
    if rules:
        cmd.extend(["-r", rules])
    if output:
        cmd.extend(["-o", output])
    cmd.append("--detailed" if detailed else "--basic")
    if strict_fixed_width:
        cmd.append("--strict-fixed-width")
        if strict_level:
            cmd.extend(["--strict-level", strict_level])
    if use_chunked:
        cmd.extend(["--use-chunked", "--chunk-size", str(chunk_size)])
        cmd.append("--progress" if progress else "--no-progress")
    return cmd


def build_compare_cmd(
    py: str,
    baseline_file: str,
    current_file: str,
    keys: str | None = None,
    mapping: str | None = None,
    output: str | None = None,
    detailed: bool = True,
    use_chunked: bool = False,
    chunk_size: int = 100000,
) -> list[str]:
    cmd = [py, "-m", "src.main", "compare", "-f1", baseline_file, "-f2", current_file]
    if keys:
        cmd.extend(["-k", keys])
    if mapping:
        cmd.extend(["-m", mapping])
    if output:
        cmd.extend(["-o", output])
    cmd.append("--detailed" if detailed else "--basic")
    if use_chunked:
        cmd.extend(["--use-chunked", "--chunk-size", str(chunk_size), "--no-progress"])
    return cmd
