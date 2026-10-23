"""The scheduled billing jobs in the Bicep must run real commands, dry by default.

`infra/modules/container-apps.bicep` schedules `scripts/stripe_step_meter.py`
and `scripts/stripe_seat_sync.py` as Container Apps jobs. Azure checks none of this until a deploy, and a
mistake is quiet: a renamed script leaves an hourly job failing in logs no one
reads, and a default flipped to live posts usage or seat counts to Stripe
before the owner has chosen to. So each job is held against the script it
runs, and its live switch against its documented default.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = [pytest.mark.unit]

ROOT = pathlib.Path(__file__).resolve().parents[3]
INFRA = ROOT / "infra"
CONTAINER_APPS = INFRA / "modules" / "container-apps.bicep"
SCRIPTS = ROOT / "backend" / "scripts"


def _job_block(source: str, symbol: str) -> str:
    start = source.index(f"resource {symbol} ")
    end = source.find("\nresource ", start + 1)
    return source[start : end if end != -1 else len(source)]


def _cron_minute(job: str) -> int:
    cron = re.search(r"cronExpression: '([^']+)'", job)
    assert cron is not None
    minute, *rest = cron.group(1).split()
    assert minute.isdigit() and rest == ["*", "*", "*", "*"]
    return int(minute)


def test_step_meter_job_runs_the_script_hourly():
    job = _job_block(CONTAINER_APPS.read_text(), "stepMeterJob")
    assert "command: ['python', '-m', 'scripts.stripe_step_meter']" in job
    assert (SCRIPTS / "stripe_step_meter.py").is_file()
    assert 0 <= _cron_minute(job) < 60


def test_step_meter_job_is_dry_unless_switched_live():
    job = _job_block(CONTAINER_APPS.read_text(), "stepMeterJob")
    assert "args: [stripeStepMeterLive ? '--live' : '--dry-run']" in job
    script = (SCRIPTS / "stripe_step_meter.py").read_text()
    assert '"--dry-run"' in script and '"--live"' in script

    assert "param stripeStepMeterLive bool = false" in (INFRA / "main.bicep").read_text()
    for env in ("dev", "prod"):
        params = (INFRA / "environments" / f"{env}.bicepparam").read_text()
        assert "param stripeStepMeterLive = false" in params, env


def test_seat_sync_job_runs_the_script_hourly_apart_from_the_step_meter():
    source = CONTAINER_APPS.read_text()
    job = _job_block(source, "seatSyncJob")
    assert "name: '${prefix}-seatsync-job'" in job
    assert "command: ['python', '-m', 'scripts.stripe_seat_sync']" in job
    assert (SCRIPTS / "stripe_seat_sync.py").is_file()
    assert _cron_minute(job) != _cron_minute(_job_block(source, "stepMeterJob"))
    assert "SLOT: seat-sync processor" not in source


def test_seat_sync_job_is_dry_unless_switched_live():
    job = _job_block(CONTAINER_APPS.read_text(), "seatSyncJob")
    assert "args: [stripeSeatSyncLive ? '--live' : '--dry-run']" in job
    script = (SCRIPTS / "stripe_seat_sync.py").read_text()
    assert '"--dry-run"' in script and '"--live"' in script

    main = (INFRA / "main.bicep").read_text()
    assert "param stripeSeatSyncLive bool = false" in main
    assert "stripeSeatSyncLive: stripeSeatSyncLive" in main
    for env in ("dev", "prod"):
        params = (INFRA / "environments" / f"{env}.bicepparam").read_text()
        assert "param stripeSeatSyncLive = false" in params, env


def test_seat_sync_job_reads_its_own_stripe_key_only_in_live_mode():
    source = CONTAINER_APPS.read_text()
    job = _job_block(source, "seatSyncJob")
    assert "concat([" in job and "], seatSyncStripeSecret)" in job
    assert "], seatSyncStripeEnv)" in job
    assert "stripe-seat-sync-key" not in job
    assert "STRIPE_SECRET_KEY" not in job
    assert "stripe-step-meter-key" not in job

    for var in ("seatSyncStripeSecret", "seatSyncStripeEnv"):
        decl = re.search(
            rf"var {var} = stripeSeatSyncLive\n  \? \[(.*?)\]\n  : \[\]\n", source, re.S
        )
        assert decl is not None, var
    assert "keyVaultUrl: '${keyVaultUri}/secrets/stripe-seat-sync-key'" in source
    assert "{ name: 'STRIPE_SECRET_KEY', secretRef: 'stripe-seat-sync-key' }" in source


def test_infra_holds_no_stripe_keys():
    pattern = re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]")
    for path in INFRA.rglob("*"):
        if path.is_file() and path.suffix in {".bicep", ".bicepparam", ".json"}:
            assert not pattern.search(path.read_text()), path


def test_live_step_meter_reads_the_pool_from_key_vault():
    """Live, the job needs the pool's size to tell overage from the pool.

    It is a commercial term, so it is a Key Vault reference by name, and no
    Bicep file sets it to a value.
    """
    source = CONTAINER_APPS.read_text()
    assert "{ name: 'GUIDED_STEPS_POOL_PER_USER', secretRef: 'guided-steps-pool-per-user' }" in (
        source
    )
    assert "/secrets/guided-steps-pool-per-user'" in source
    for path in INFRA.rglob("*"):
        if path.is_file() and path.suffix in {".bicep", ".bicepparam", ".json"}:
            text = path.read_text()
            assert not re.search(r"GUIDED_STEPS_\w+'\s*,\s*value:", text), path
