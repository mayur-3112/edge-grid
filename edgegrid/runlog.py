"""Run records: every experiment writes to its own directory, never over another.

The Phase-1 harness deleted prior results on each run and recorded neither the
judge backend nor the generator model, so its numbers could not be reproduced or
even attributed. Everything here exists to make that impossible:

  * results land in `docs/results/<run_id>/`, never a shared filename,
  * `config.json` captures the full config snapshot plus the git SHA,
  * `manifest.json` records counts, timing, and any dropped rows,
  * CSV rows are appended as they are produced, so a crash keeps the partial run.
"""

from __future__ import annotations

import csv
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from edgegrid import config as C


def git_sha(short: bool = True) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short" if short else "HEAD", "HEAD"],
            cwd=C.REPO_ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=C.REPO_ROOT,
                             capture_output=True, text=True, timeout=5)
        return bool(out.stdout.strip())
    except Exception:
        return False


class RunLog:
    """One experiment run. Use as a context manager."""

    def __init__(self, experiment: str, params: Optional[dict] = None,
                 results_dir: Optional[Path] = None):
        self.experiment = experiment
        self.params = params or {}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = Path(results_dir) if results_dir else C.RESULTS_DIR
        base.mkdir(parents=True, exist_ok=True)
        # Second-resolution timestamps collide when two runs start in the same
        # second, which would silently overwrite the first run's results - the
        # exact failure this class exists to prevent. Claim the directory
        # atomically and suffix until we win.
        self.run_id = f"{experiment}-{stamp}"
        self.dir = base / self.run_id
        suffix = 1
        while True:
            try:
                self.dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                self.run_id = f"{experiment}-{stamp}-{suffix}"
                self.dir = base / self.run_id
                suffix += 1
        self._t0 = time.monotonic()
        self._counts: dict[str, int] = {}
        self._dropped: list[dict] = []
        self._writers: dict[str, Any] = {}
        self._files: dict[str, Any] = {}
        self._write_config()

    # -- context ---------------------------------------------------------

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.finish(error=None if exc is None else f"{exc_type.__name__}: {exc}")

    # -- config ----------------------------------------------------------

    def _write_config(self) -> None:
        payload = {
            "run_id": self.run_id,
            "experiment": self.experiment,
            "params": self.params,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "git_sha": git_sha(),
            "git_dirty": git_dirty(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "hostname": platform.node(),
            "config": C.snapshot(),
        }
        (self.dir / "config.json").write_text(json.dumps(payload, indent=2))

    # -- rows ------------------------------------------------------------

    def append(self, table: str, row: dict) -> None:
        """Append one row to `<table>.csv`, writing the header on first use.

        The header is fixed by the first row; later rows are projected onto it so
        a stray extra key cannot corrupt the file silently - it is dropped and
        counted instead."""
        path = self.dir / f"{table}.csv"
        if table not in self._writers:
            f = path.open("a", newline="", encoding="utf-8")
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if path.stat().st_size == 0:
                w.writeheader()
            self._files[table], self._writers[table] = f, w
        w = self._writers[table]
        extra = set(row) - set(w.fieldnames)
        if extra:
            self.drop(table, f"row had unexpected keys {sorted(extra)}")
            row = {k: v for k, v in row.items() if k in w.fieldnames}
        w.writerow({k: row.get(k, "") for k in w.fieldnames})
        self._files[table].flush()
        self._counts[table] = self._counts.get(table, 0) + 1

    def write_table(self, table: str, rows: list[dict]) -> Path:
        path = self.dir / f"{table}.csv"
        if not rows:
            path.write_text("")
            return path
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        self._counts[table] = len(rows)
        return path

    def write_json(self, name: str, payload: Any) -> Path:
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, default=str))
        return path

    def drop(self, what: str, why: str) -> None:
        """Record a case that was skipped. Never silently under-report N."""
        self._dropped.append({"what": what, "why": why, "at": time.monotonic() - self._t0})

    def note(self, msg: str) -> None:
        with (self.dir / "log.txt").open("a") as f:
            f.write(f"[{time.monotonic() - self._t0:8.2f}s] {msg}\n")

    # -- finish ----------------------------------------------------------

    def finish(self, error: Optional[str] = None) -> Path:
        for f in self._files.values():
            try:
                f.close()
            except Exception:
                pass
        manifest = {
            "run_id": self.run_id,
            "experiment": self.experiment,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self._t0, 3),
            "rows": self._counts,
            "dropped": self._dropped,
            "n_dropped": len(self._dropped),
            "error": error,
            "status": "error" if error else "ok",
        }
        (self.dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        self._update_index(manifest)
        return self.dir

    def _update_index(self, manifest: dict) -> None:
        """Maintain docs/results/index.jsonl so every run ever made is listed."""
        idx = self.dir.parent / "index.jsonl"
        with idx.open("a") as f:
            f.write(json.dumps({k: manifest[k] for k in
                                ("run_id", "experiment", "finished_utc",
                                 "elapsed_s", "rows", "n_dropped", "status")}) + "\n")

    @staticmethod
    def latest(experiment: str, results_dir: Optional[Path] = None) -> Optional[Path]:
        """Most recent successful run directory for an experiment."""
        base = Path(results_dir) if results_dir else C.RESULTS_DIR
        cands = sorted(p for p in base.glob(f"{experiment}-*") if (p / "manifest.json").exists())
        for p in reversed(cands):
            if json.loads((p / "manifest.json").read_text()).get("status") == "ok":
                return p
        return None
