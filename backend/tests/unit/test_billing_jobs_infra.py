"""The scheduled billing jobs in the Bicep must run real commands, dry by default.

`infra/modules/container-apps.bicep` schedules `scripts/stripe_step_meter.py`
as a Container Apps job. Azure checks none of this until a deploy, and a
mistake is quiet: a renamed script leaves an hourly job failing in logs no one
reads, and a default flipped to live posts usage to Stripe before the owner
has chosen to. So the job is held against the script it runs, and the live
switch against its documented default.
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


def test_step_meter_job_runs_the_script_hourly():
    job = _job_block(CONTAINER_APPS.read_text(), "stepMeterJob")
    assert "command: ['python', '-m', 'scripts.stripe_step_meter']" in job
    assert (SCRIPTS / "stripe_step_meter.py").is_file()
    cron = re.search(r"cronExpression: '([^']+)'", job)
    assert cron is not None
    minute, *rest = cron.group(1).split()
    assert minute.isdigit() and rest == ["*", "*", "*", "*"]


def test_step_meter_job_is_dry_unless_switched_live():
    job = _job_block(CONTAINER_APPS.read_text(), "stepMeterJob")
    assert "args: [stripeStepMeterLive ? '--live' : '--dry-run']" in job
    script = (SCRIPTS / "stripe_step_meter.py").read_text()
    assert '"--dry-run"' in script and '"--live"' in script

    assert "param stripeStepMeterLive bool = false" in (INFRA / "main.bicep").read_text()
    for env in ("dev", "prod"):
        params = (INFRA / "environments" / f"{env}.bicepparam").read_text()
        assert "param stripeStepMeterLive = false" in params, env


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
