"""Runs sync and maintenance at their scheduled times, one at a time.

    python3 -m gogkeeper.schedule

Needs GOGKEEPER_SYNC_SCHEDULE and GOGKEEPER_MAINTENANCE_SCHEDULE, in cron's five-field
format, e.g. "0 0 * * *". Times follow TZ. Starting never triggers a run, and a run missed
while the scheduler was down waits for its next time. A job that comes due while another is
running waits for it to finish. A failed job is logged and the scheduler carries on.
If the share isn't mounted, at the start or when a job comes due, the scheduler exits
with an error, so the container shows as restarting instead of failing quietly.
"""

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_SUBMITTED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from gogkeeper import first_run
from gogkeeper.sync import setting

ENTRYPOINT = "/usr/local/bin/docker-entrypoint.sh"
WEB_PORT = 7157


def log(message):
    print(f"gogkeeper: {datetime.now():%Y-%m-%d %H:%M:%S}: {message}", file=sys.stderr, flush=True)


def check_share():
    """Exit if the share isn't mounted. Not sys.exit: that would only end a job's thread."""
    if not first_run.LIBRARY.is_dir():
        log(f"library folder not found: {first_run.LIBRARY}. Is the share mounted? Stopping")
        os._exit(1)


def how_long(seconds):
    return str(timedelta(seconds=int(seconds)))


class Jobs:
    """Runs each job and logs what happened. APScheduler does the timing and the queue."""

    def __init__(self):
        self.running = None
        self.waiting_since = {}  # job name -> when it came due, for jobs held up by another

    def submitted(self, event):
        # APScheduler sends this after handing over the job, which may already have started.
        if self.running not in (None, event.job_id):
            self.waiting_since[event.job_id] = event.scheduled_run_times[-1]
            log(f"{event.job_id} is due, waiting for {self.running} to finish")

    def skipped(self, event):
        log(f"{event.job_id} is due, but one is already running or waiting, so this run is skipped")

    def run(self, name):
        check_share()
        self.running = name
        due = self.waiting_since.pop(name, None)
        if due:
            log(f"{name} starting, {how_long((datetime.now().astimezone() - due).total_seconds())} late")
        else:
            log(f"{name} starting")
        started = time.monotonic()
        try:
            code = subprocess.run([ENTRYPOINT, name], stdin=subprocess.DEVNULL).returncode
            took = how_long(time.monotonic() - started)
            if code == 0:
                log(f"{name} finished after {took}")
            else:
                log(f"{name} FAILED (exit {code}) after {took}. It runs again at its next scheduled time")
        except OSError as e:
            log(f"{name} FAILED: couldn't start it: {e}. It runs again at its next scheduled time")
        finally:
            self.running = None

    def status(self):
        """The job running, if any, and the jobs waiting for it. For the web page."""
        # A copy first: the scheduler's thread may change it meanwhile.
        return {"running": self.running, "waiting": sorted(self.waiting_since.copy())}


def build(triggers):
    jobs = Jobs()
    scheduler = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(1)},  # one job at a time; the rest wait their turn
        job_defaults={
            "misfire_grace_time": None,  # a job held up by another still runs, however late
            "max_instances": 1,  # never more than one of the same job running or waiting
            "coalesce": True,
        },
    )
    scheduler.add_listener(jobs.submitted, EVENT_JOB_SUBMITTED)
    scheduler.add_listener(jobs.skipped, EVENT_JOB_MAX_INSTANCES)
    for name, trigger in triggers.items():
        scheduler.add_job(jobs.run, trigger, args=[name], id=name)
    return scheduler, jobs


def read_schedule(variable):
    expression = setting(variable)
    fields = expression.split()
    try:
        if len(fields) == 5:
            # Both of these would otherwise run on the wrong days, without any error.
            if re.search(r"\d", fields[4]):
                raise ValueError("use names for the day of week, such as mon or sun: "
                                 "numbers would mean different days from cron's")
            if fields[2] != "*" and fields[4] != "*":
                raise ValueError("set the day of month or the day of week, not both")
        return CronTrigger.from_crontab(expression)
    except ValueError as e:
        raise RuntimeError(f"{variable} is invalid: {e}") from None


def stop(_signal, _frame):
    log("scheduler stopping")
    # Not sys.exit: that waits for a running job's thread, and docker stop would time out.
    os._exit(0)


def start_web_page(scheduler, jobs):
    """Serve the web page from this container, in the background.

    If it can't start, such as when the port is taken, it says so and the schedule carries on.
    The page's Sync button brings the scheduled sync forward to now, so it joins the same queue:
    it waits for a running job, and isn't queued twice.
    """
    import uvicorn

    from gogkeeper import web

    def sync_now():
        log("sync requested from the web page")
        scheduler.modify_job("sync", next_run_time=datetime.now().astimezone())

    web.sync_now = sync_now
    web.job_status = jobs.status
    server = uvicorn.Server(uvicorn.Config("gogkeeper.web:app", host="0.0.0.0", port=WEB_PORT,
                                           log_level="warning"))
    threading.Thread(target=server.run, name="web", daemon=True).start()
    log(f"web page starting on port {WEB_PORT}")


def main():
    triggers = {
        "sync": read_schedule("GOGKEEPER_SYNC_SCHEDULE"),
        "maintenance": read_schedule("GOGKEEPER_MAINTENANCE_SCHEDULE"),
    }
    # Without these, as the container's main process it would ignore docker stop.
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    check_share()
    logging.getLogger("apscheduler").setLevel(logging.ERROR)  # its routine messages are replaced by ours
    log("scheduler started: sync at '{}', maintenance at '{}'".format(
        setting("GOGKEEPER_SYNC_SCHEDULE"), setting("GOGKEEPER_MAINTENANCE_SCHEDULE")))
    scheduler, jobs = build(triggers)
    start_web_page(scheduler, jobs)
    scheduler.start()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"gogkeeper: scheduler failed: {e}", file=sys.stderr)
        sys.exit(1)
