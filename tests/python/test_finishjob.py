"""script/finishjob.py: a job waiting for rear pages converts at once (API, Image button)."""
import json

import pytest

import finishjob
import jobstate
import scanner


@pytest.fixture
def jobs(clean_env, tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    clean_env.setenv("SCANNER_JOBS_DIR", str(jobs_dir))
    monkeypatch.setattr(scanner.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(finishjob, "read_environment", lambda: None)

    def add(job, state, workdir=True):
        (jobs_dir / f"{job}.json").write_text(json.dumps({"job": job, "state": state}))
        if workdir:
            (tmp_path / job).mkdir()
        return tmp_path / job / scanner.FINISH_FLAG
    return add


def test_finishes_the_named_waiting_job(jobs, tmp_path):
    flag = jobs("2026-01-01-10-00-00", "waiting_for_rear")
    assert finishjob.main(["--job-id", "2026-01-01-10-00-00"], log_path=str(tmp_path / "log")) == 0
    assert flag.exists()


def test_without_an_id_the_newest_waiting_job(jobs, tmp_path):
    older = jobs("2026-01-01-10-00-00", "waiting_for_rear")
    jobs("2026-01-01-11-00-00", "done", workdir=False)
    newer = jobs("2026-01-01-10-30-00", "waiting_for_rear")
    assert finishjob.main(["brother4:net1;dev0", "PersonA"], log_path=str(tmp_path / "log")) == 0
    assert newer.exists() and not older.exists()


def test_nothing_waiting(jobs, tmp_path):
    jobs("2026-01-01-10-00-00", "done", workdir=False)
    assert finishjob.main([], log_path=str(tmp_path / "log")) == finishjob.NOT_WAITING
    assert finishjob.main(["--job-id", "2026-01-01-10-00-00"], log_path=str(tmp_path / "log")) == finishjob.NOT_WAITING


def test_invalid_job_id_is_refused(jobs, tmp_path):
    assert finishjob.main(["--job-id", "../../etc"], log_path=str(tmp_path / "log")) == 2


def test_all_waiting_jobs_are_finished_on_stop(jobs, tmp_path):
    a = jobs("2026-01-01-10-00-00", "waiting_for_rear")
    jobs("2026-01-01-10-10-00", "done", workdir=False)
    b = jobs("2026-01-01-10-20-00", "waiting_for_rear")
    assert finishjob.main(["--all"], log_path=str(tmp_path / "log")) == 0
    assert a.exists() and b.exists()


def test_all_with_nothing_waiting_is_fine(jobs, tmp_path):
    assert finishjob.main(["--all"], log_path=str(tmp_path / "log")) == 0
