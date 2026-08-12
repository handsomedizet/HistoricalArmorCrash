from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import math
import os
import re
import shutil
import subprocess
import time

from .config import SolverConfig


NORMAL_TERMINATION = re.compile(
    r"normal\s+termination|n\s*o\s*r\s*m\s*a\s*l\s+t\s*e\s*r\s*m\s*i\s*n\s*a\s*t\s*i\s*o\s*n",
    re.IGNORECASE,
)
FATAL_MARKERS = re.compile(
    r"^\s*\*{3}\s*error\b"
    r"|\b(?:fatal|input)\s+error\b"
    r"|\berror\s+termination\b"
    r"|e\s*r\s*r\s*o\s*r\s+t\s*e\s*r\s*m\s*i\s*n\s*a\s*t\s*i\s*o\s*n",
    re.IGNORECASE,
)
EXPLANATORY_ERROR_TERMINATION = re.compile(r"\berror\s+termination\s+if\b", re.IGNORECASE)


@dataclass(frozen=True)
class RunResult:
    case_id: str
    status: str
    return_code: int | None
    elapsed_s: float
    message: str


def resolve_executable(configured: str) -> Path | None:
    value = os.path.expandvars(configured.strip().strip('"'))
    if value:
        path = Path(value).expanduser()
        if path.is_file():
            return path.resolve()
        found = shutil.which(value)
        if found:
            return Path(found).resolve()
        return None
    for name in ("ls-dyna_smp_d.exe", "ls-dyna_smp_s.exe", "lsdyna.exe", "ls-dyna.exe"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def solver_command(executable: Path, case_dir: Path, solver: SolverConfig) -> list[str]:
    name = executable.name.casefold()
    single_precision = bool(re.search(r"(?:smp|mpp)[_-]s(?:[_\-.]|$)", name))
    bytes_per_word = 4 if single_precision else 8
    memory_mwords = max(1, math.ceil(solver.memory_mb / bytes_per_word))
    return [
        str(executable),
        "i=run.k",
        f"ncpus={solver.ncpus}",
        f"memory={memory_mwords}m",
    ]


def _read_solver_text(case_dir: Path) -> str:
    chunks: list[str] = []
    for name in ("solver.log", "d3hsp", "messag"):
        path = case_dir / name
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    for path in sorted(case_dir.glob("mes*")):
        if path.is_file() and path.name != "messag":
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def _has_fatal_marker(text: str) -> bool:
    for line in text.splitlines():
        if EXPLANATORY_ERROR_TERMINATION.search(line):
            continue
        if FATAL_MARKERS.search(line):
            return True
    return False


def _fatal_message(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.search(r"\*{3}\s*Error\s+([^\r\n]+)", line, re.IGNORECASE)
        if match is None:
            continue
        detail = f"LS-DYNA Error {match.group(1).strip()}"
        for following in lines[index + 1:index + 4]:
            message = following.strip()
            if message:
                return f"{detail}: {message}"
        return detail
    return "LS-DYNA reported an input or fatal error"


def inspect_case(case_dir: Path, return_code: int | None = None) -> tuple[str, str]:
    text = _read_solver_text(case_dir)
    if _has_fatal_marker(text):
        return "failed", _fatal_message(text)
    if NORMAL_TERMINATION.search(text):
        if (case_dir / "nodout").is_file() or (case_dir / "binout").is_file():
            return "completed", "Normal termination and result output detected"
        return "completed_no_history", "Normal termination, but NODOUT/BINOUT was not found"
    if return_code not in (None, 0):
        return "failed", f"Solver exited with code {return_code}"
    if any(case_dir.glob("d3plot*")):
        return "incomplete", "D3PLOT exists, but normal termination was not confirmed"
    return "unknown", "No normal-termination marker or result database found"


def run_case(case_dir: Path, solver: SolverConfig, *, dry_run: bool = False) -> RunResult:
    executable = resolve_executable(solver.executable)
    if dry_run and executable is None and solver.executable.strip():
        executable = Path(os.path.expandvars(solver.executable.strip().strip('"'))).expanduser()
    if executable is None:
        return RunResult(case_dir.name, "blocked", None, 0.0, "LS-DYNA executable was not found")
    command = solver_command(executable, case_dir, solver)
    if dry_run:
        return RunResult(case_dir.name, "dry_run", None, 0.0, subprocess.list2cmdline(command))

    started = time.perf_counter()
    log_path = case_dir / "solver.log"
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            completed = subprocess.run(
                command,
                cwd=case_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=solver.timeout_minutes * 60,
            )
        status, message = inspect_case(case_dir, completed.returncode)
        return RunResult(case_dir.name, status, completed.returncode, time.perf_counter() - started, message)
    except subprocess.TimeoutExpired:
        return RunResult(
            case_dir.name,
            "timeout",
            None,
            time.perf_counter() - started,
            f"Exceeded {solver.timeout_minutes:g} minutes",
        )
    except OSError as exc:
        return RunResult(case_dir.name, "failed", None, time.perf_counter() - started, str(exc))


def run_study(study_dir: str | Path, solver: SolverConfig, *, dry_run: bool = False) -> list[RunResult]:
    root = Path(study_dir)
    manifest = root / "manifest.csv"
    if not manifest.is_file():
        raise FileNotFoundError(f"Study manifest not found: {manifest}")
    with manifest.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    results: list[RunResult] = []
    for row in rows:
        case_dir = root / row["case_id"]
        result = run_case(case_dir, solver, dry_run=dry_run)
        results.append(result)
        row["status"] = result.status
        row["elapsed_s"] = f"{result.elapsed_s:.3f}"
        row["run_message"] = result.message
    if not dry_run and rows:
        fields = list(rows[0])
        with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return results
