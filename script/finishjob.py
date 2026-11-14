#!/usr/bin/env python3
"""Finish a scan job that waits for rear pages now, without the rear pages.

After the front pages a job waits REAR_PAGE_WAIT_SECONDS for a rear scan
before it converts to PDF. This asks the waiting process to convert at once,
so single-sided documents can be scanned one after the other.

Called by the API (POST /api/jobs/<id>/finish, POST /api/scanner/finish) with
--job-id, and with IMAGE_BUTTON=finish by the scanner's Image button as
"<script> <device> <friendly name>" (then the newest waiting job is meant).

Exit status: 0 finished, 3 no such job waiting for rear pages, 2 bad arguments.
"""

import argparse
import os
import sys
from typing import List, Optional

import jobstate
from scanner import JOB_NAME_PATTERN, finish_flag_path, read_environment

LOG_PATH = "/var/log/scanner.log"
NOT_WAITING = 3


def waiting_job(job_id: Optional[str]) -> Optional[str]:
    """The job to finish: the given one if it waits for rear pages, else the newest waiting job."""
    if job_id is not None:
        record = jobstate.read(job_id) or {}
        return job_id if record.get("state") == "waiting_for_rear" else None
    for record in jobstate.list_jobs():
        if record.get("state") == "waiting_for_rear":
            return record.get("job")
    return None


def finish(job_id: Optional[str]) -> int:
    job = waiting_job(job_id)
    if job is None or not JOB_NAME_PATTERN.match(job):
        print("finish: no job is waiting for rear pages")
        return NOT_WAITING
    flag = finish_flag_path(job)
    if not os.path.isdir(os.path.dirname(flag)):
        print(f"finish: job {job} has no working directory any more")
        return NOT_WAITING
    with open(flag, "w") as handle:
        handle.write("finish\n")
    print(f"finish: job {job} converts now, without rear pages")
    return 0


def main(argv: List[str], log_path: str = LOG_PATH) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("device", nargs="?", default=None)
    parser.add_argument("friendly_name", nargs="?", default=None)
    parser.add_argument("--job-id", dest="job_id", default=None)
    args, _unknown = parser.parse_known_args(argv)
    if args.job_id is not None and not JOB_NAME_PATTERN.match(args.job_id):
        print(f"finish: invalid --job-id {args.job_id!r}", file=sys.stderr)
        return 2
    stdout = sys.stdout
    with open(log_path, "a") as log:
        sys.stdout = log
        try:
            try:
                read_environment()
            except OSError:
                pass  # env.txt only matters for the jobs directory override
            return finish(args.job_id)
        finally:
            sys.stdout = stdout


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
