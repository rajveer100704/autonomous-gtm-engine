"""
Background scheduler process. Run this alongside the API
(`python -m gtm_engine.scheduler`) in production, or trigger
POST /run-followups from an external cron (Render Cron Job, GitHub
Actions schedule, etc.) if you'd rather not run a long-lived process.
"""
import time
import logging
from apscheduler.schedulers.blocking import BlockingScheduler

from gtm_engine.agents import followup_agent
from gtm_engine.crm import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gtm_scheduler")


def _dry_run_sender(outreach_row: dict) -> bool:
    log.info("[DRY RUN SEND] outreach_id=%s -> %s", outreach_row["id"], outreach_row["subject"])
    return True


def tick():
    db.init_db()
    results = followup_agent.run_due_followups(_dry_run_sender)
    log.info("Follow-up tick processed %d items", len(results))


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(tick, "interval", minutes=30, next_run_time=None)
    log.info("Scheduler started — checking for due follow-ups every 30 minutes")
    scheduler.start()
