"""Offline regression tests for deployment and evidence tooling."""

from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_HARNESS = ROOT / "deploy" / "001_deploy_and_smoke.py"
EVIDENCE_VERIFIER = ROOT / "scripts" / "verify_evidence.py"
LEGACY_EVIDENCE = ROOT / "deployments" / "studionet.json"


@pytest.fixture(scope="module")
def deploy_module():
    return runpy.run_path(str(DEPLOY_HARNESS), run_name="theater_deploy_preflight")


def test_deploy_harness_has_closed_network_and_lifecycle_fixtures(deploy_module):
    assert deploy_module["ALLOWED_NETWORKS"] == {"localnet", "testnet_bradbury"}
    assert deploy_module["STEPS"] == [
        "authorize_reporter",
        "register_cue_open",
        "register_cue_cross",
        "register_cue_close",
        "seal_cue_sheet",
        "report_incident",
        "triage_incident",
        "choose_recovery",
        "acknowledge_recovery",
    ]
    expected = deploy_module["EXPECTED"]
    assert expected["decision_status"] == "MATCHED"
    assert expected["anchor_index"] == 1
    assert expected["anchor_cue"] == "CUE-CROSS"
    assert expected["impact"] == "LOCAL"


def test_deploy_harness_preflights_abi_and_requires_canonical_digests(deploy_module):
    assert deploy_module["_abi_preflight"]() is None
    digest = "a" * 64
    assert deploy_module["_digest"](digest, "fixture") == digest
    for malformed in (None, "a" * 63, "A" * 64, "g" * 64):
        with pytest.raises(AssertionError, match="lowercase 32-byte digest"):
            deploy_module["_digest"](malformed, "fixture")


def test_deploy_harness_pins_final_reads_and_scopes_gas_override():
    source = DEPLOY_HARNESS.read_text(encoding="utf-8")
    assert "TransactionHashVariant.LATEST_FINAL" in source
    assert "THEATER_BRADBURY_DEPLOY_GAS_OVERRIDE" in source
    assert 'os.environ.get("THEATER_BRADBURY_DEPLOY_GAS_OVERRIDE") == "1"' in source


def verifier(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EVIDENCE_VERIFIER), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_evidence_verifier_self_test_covers_eight_cases():
    result = verifier("--self-test")
    assert result.returncode == 0, result.stdout
    assert "SELF-TEST PASS: 8 cases" in result.stdout


def test_legacy_evidence_is_accepted_only_without_evidence_grade_requirement():
    legacy = verifier(str(LEGACY_EVIDENCE))
    assert legacy.returncode == 0, legacy.stdout
    assert "PASS [legacy-v1]" in legacy.stdout
    assert "WARNING:" in legacy.stdout

    strict = verifier(str(LEGACY_EVIDENCE), "--require-evidence-grade")
    assert strict.returncode == 1, strict.stdout
    assert "FAIL [legacy-v1]" in strict.stdout
    assert "require-evidence-grade" in strict.stdout
