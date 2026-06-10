"""Scheduler P3 — process APScheduler unique remplaçant interim_loop.

Cadences dans api/config/scheduler.yaml (committé). L'AIS reste un daemon
séparé (service rouge-ais). Chaque job est isolé : une exception est
loggée, le scheduler continue.

Lancement : ./venv/bin/python -m collectors.scheduler
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from collectors.base import REPO_ROOT, get_logger
from collectors.notify import send

CFG_PATH = REPO_ROOT / "api" / "config" / "scheduler.yaml"
log = get_logger("scheduler")


def _run_collector(name: str) -> None:
    """Import paresseux : un module collecteur cassé ne tue pas le process."""
    import importlib
    mapping = {
        "pm": ("collectors.pm_collector", "PmCollector"),
        "news": ("collectors.news_collector", "NewsCollector"),
        "flights": ("collectors.flights_collector", "FlightsCollector"),
        "macro": ("collectors.macro_collector", "MacroCollector"),
        "cot": ("collectors.cot_collector", "CotCollector"),
    }
    mod, cls = mapping[name]
    ok = getattr(importlib.import_module(mod), cls)().run()
    if not ok:
        log.error("job en échec (stale servi)", extra={"ctx": {"job": name}})


def job_quotes_chain() -> None:
    """Agrégation daily depuis le cache M5 (rsync Mac) puis rebuild des
    modules dérivés — l'ordre compte (trend/fx/saison/tdi lisent daily)."""
    import importlib
    chain = [("collectors.quotes_collector", "QuotesCollector"),
             ("collectors.trend_builder", "TrendBuilder"),
             ("collectors.fx_builder", "FxBuilder"),
             ("collectors.season_builder", "SeasonBuilder"),
             ("collectors.tdi_builder", "TdiBuilder"),
             ("collectors.micro_builder", "MicroBuilder")]
    for mod, cls in chain:
        try:
            if not getattr(importlib.import_module(mod), cls)().run():
                log.error("chaîne quotes : étape en échec", extra={"ctx": {"step": cls}})
        except Exception:
            log.error("chaîne quotes : étape plantée", exc_info=True,
                      extra={"ctx": {"step": cls}})


def job_retention() -> None:
    from scripts.retention import run_retention
    run_retention()


def dataset_age_s(ds: str) -> float | None:
    root = REPO_ROOT / "data" / ds
    files = [p for p in root.rglob("*.parquet")] if root.exists() else []
    if not files:
        return None
    return time.time() - max(p.stat().st_mtime for p in files)


def job_stale_check() -> None:
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    bad = []
    for ds, limit in cfg["stale_limits_s"].items():
        age = dataset_age_s(ds)
        if age is None or age > limit:
            bad.append(f"{ds} (âge {'∅' if age is None else f'{age/3600:.1f}h'}, "
                       f"limite {limit/3600:.0f}h)")
    if bad:
        msg = "datasets critiques stale : " + " ; ".join(bad)
        log.error(msg)
        send(msg, level="error")


def main() -> None:
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    sched = BlockingScheduler(timezone="UTC",
                              job_defaults={"coalesce": True,
                                            "misfire_grace_time": 300,
                                            "max_instances": 1})
    funcs = {
        "pm": lambda: _run_collector("pm"),
        "news": lambda: _run_collector("news"),
        "flights": lambda: _run_collector("flights"),
        "macro": lambda: _run_collector("macro"),
        "cot": lambda: _run_collector("cot"),
        "cot_retry": lambda: _run_collector("cot"),
        "quotes_chain": job_quotes_chain,
        "retention": job_retention,
        "stale_check": job_stale_check,
    }
    for name, spec in cfg["jobs"].items():
        if "cron" in spec:
            m, h, dom, mon, dow = spec["cron"].split()
            trig = CronTrigger(minute=m, hour=h, day=dom, month=mon,
                               day_of_week=dow, timezone="UTC")
        elif "every_minutes" in spec:
            trig = IntervalTrigger(minutes=spec["every_minutes"])
        else:
            trig = IntervalTrigger(hours=spec["every_hours"])
        sched.add_job(funcs[name], trig, id=name, name=name)
        log.info("job planifié", extra={"ctx": {"job": name, "spec": spec}})
    log.info("scheduler démarré", extra={"ctx": {"jobs": len(cfg["jobs"])}})
    sched.start()


if __name__ == "__main__":
    main()
