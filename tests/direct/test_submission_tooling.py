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


@pytest.fixture(scope="module")
def evidence_module():
    return runpy.run_path(str(EVIDENCE_VERIFIER), run_name="theater_evidence_preflight")


def test_deploy_harness_has_closed_network_and_lifecycle_fixtures(deploy_module):
    assert deploy_module["ALLOWED_NETWORKS"] == {
        "localnet",
        "studionet",
        "testnet_bradbury",
    }
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


def test_runner_header_is_followed_by_module_docstring_not_comment():
    lines = (ROOT / "contracts" / "theater_cue_recovery.py").read_text(
        encoding="utf-8"
    ).splitlines()
    assert lines[0].startswith('# { "Depends": "py-genlayer:')
    assert lines[1].startswith('"""')


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


def test_deploy_harness_recursively_redacts_receipt_credentials(deploy_module):
    receipt = {
        "consensus_data": {
            "validators": [
                {
                    "node_config": {
                        "private_key": "secret-a",
                        "privateKey": "secret-b",
                        "client_secret": "secret-c",
                        "mnemonic": "secret-d",
                        "password": "secret-e",
                        "authorization": "secret-f",
                        "public_key": "safe-public-key",
                    }
                }
            ]
        }
    }
    redacted, paths = deploy_module["_redact_receipt"](receipt)
    node = redacted["consensus_data"]["validators"][0]["node_config"]
    assert node == {
        "private_key": "[REDACTED]",
        "privateKey": "[REDACTED]",
        "client_secret": "[REDACTED]",
        "mnemonic": "[REDACTED]",
        "password": "[REDACTED]",
        "authorization": "[REDACTED]",
        "public_key": "safe-public-key",
    }
    prefix = "receipt.consensus_data.validators[0].node_config."
    assert set(paths) == {
        prefix + "private_key",
        prefix + "privateKey",
        prefix + "client_secret",
        prefix + "mnemonic",
        prefix + "password",
        prefix + "authorization",
    }
    source = DEPLOY_HARNESS.read_text(encoding="utf-8")
    assert "unredacted_receipt_sha256" in source
    assert "RECURSIVE_CREDENTIAL_KEYS_V1" in source


def test_deploy_harness_binds_genvm_chain_id_without_overwriting_outer_domains(
    deploy_module,
):
    record = {
        "configured_chain_id": 4_221,
        "outer_rpc_chain_id": 4_221,
        "genvm_chain_id": None,
        "network": {
            "configured_chain_id": 4_221,
            "outer_rpc_chain_id": 4_221,
            "outer_rpc_chain_id_verified": True,
            "genvm_chain_id": None,
            "genvm_chain_id_source": "PENDING_DEPLOYMENT_READBACK",
        },
    }
    views = {
        "policy": {"deployment_chain_id": 1},
        "rehearsal": {"deployment_chain_id": 1},
    }
    assert deploy_module["_bind_genvm_chain_domain"](record, views) == 1
    assert record["configured_chain_id"] == 4_221
    assert record["outer_rpc_chain_id"] == 4_221
    assert record["genvm_chain_id"] == 1
    assert record["network"]["configured_chain_id"] == 4_221
    assert record["network"]["outer_rpc_chain_id"] == 4_221
    assert record["network"]["genvm_chain_id"] == 1
    assert record["network"]["genvm_chain_id_source"] == (
        "get_policy.deployment_chain_id|get_rehearsal.deployment_chain_id"
    )
    assert "chain_id" not in record
    assert "chain_id" not in record["network"]

    with pytest.raises(AssertionError, match="GenVM chain IDs are invalid or inconsistent"):
        deploy_module["_bind_genvm_chain_domain"](
            record,
            {
                "policy": {"deployment_chain_id": 1},
                "rehearsal": {"deployment_chain_id": 2},
            },
        )


def test_deploy_harness_binds_genvm_contract_without_overwriting_routable_address(
    deploy_module,
):
    routable = "0x" + "11" * 20
    genvm = "0x" + "22" * 20
    record = {
        "contract_address": routable,
        "contract_address_domain": "OUTER_ROUTABLE",
        "genvm_contract_address": None,
        "genvm_contract_address_source": "PENDING_DEPLOYMENT_READBACK",
        "genvm_contract_address_verified_by_readbacks": False,
    }
    readbacks = {
        "policy": {"deployment_contract_address": genvm.upper().replace("0X", "0x")},
        "rehearsal": {"deployment_contract_address": genvm},
    }

    assert deploy_module["_bind_genvm_contract_domain"](record, readbacks) == genvm
    assert record["contract_address"] == routable
    assert record["contract_address_domain"] == "OUTER_ROUTABLE"
    assert record["genvm_contract_address"] == genvm
    assert record["genvm_contract_address_source"] == (
        "get_policy.deployment_contract_address|"
        "get_rehearsal.deployment_contract_address"
    )
    assert record["genvm_contract_address_verified_by_readbacks"] is True

    with pytest.raises(
        AssertionError,
        match="policy and rehearsal GenVM contract addresses are inconsistent",
    ):
        deploy_module["_bind_genvm_contract_domain"](
            record,
            {
                "policy": {"deployment_contract_address": genvm},
                "rehearsal": {"deployment_contract_address": "0x" + "33" * 20},
            },
        )


def test_deploy_harness_records_outer_rpc_and_configured_chain_ids_independently(
    deploy_module, monkeypatch
):
    class Chain:
        id = 61_999
        name = "localnet"
        rpc_urls = {"default": {"http": ["http://127.0.0.1:4000/api"]}}
        block_explorers = {"default": {"url": "http://127.0.0.1:4000"}}
        default_number_of_initial_validators = 5

    class General:
        def __init__(self, name):
            self.name = name

        def get_chain(self):
            return Chain()

        def get_network_name(self):
            return self.name

    class Provider:
        def __init__(self, chain_id):
            self.chain_id = chain_id

        def make_request(self, method, args):
            assert (method, args) == ("eth_chainId", [])
            return {"result": hex(self.chain_id)}

    class Client:
        def __init__(self, chain_id):
            self.provider = Provider(chain_id)

    network_record = deploy_module["_network_record"]
    monkeypatch.setitem(network_record.__globals__, "get_gl_client", lambda: Client(61_127))
    local = network_record(General("localnet"))
    assert local["configured_chain_id"] == 61_999
    assert local["outer_rpc_chain_id"] == 61_127
    assert local["outer_rpc_chain_id_verified"] is True
    assert local["outer_rpc_chain_id_matches_config"] is False
    assert local["outer_rpc_equality_required"] is False
    assert local["genvm_chain_id"] is None
    assert "chain_id" not in local

    with pytest.raises(
        AssertionError,
        match="testnet_bradbury outer RPC chain ID differs from configuration",
    ):
        network_record(General("testnet_bradbury"))

    with pytest.raises(
        AssertionError,
        match="studionet outer RPC chain ID differs from configuration",
    ):
        network_record(General("studionet"))


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


def test_evidence_chain_ids_keep_outer_rpc_and_genvm_message_domains_separate(
    evidence_module,
):
    data = {
        "network": {
            "chain_id": 4_221,
            "configured_chain_id": 4_221,
            "message_chain_id": 1,
        },
        "latest_final": {
            "readbacks": {
                "get_policy": {"result": {"deployment_chain_id": 1}},
                "get_rehearsal": {"result": {"deployment_chain_id": 1}},
            }
        },
    }
    report = evidence_module["Report"]("outer-4221-genvm-1")
    evidence_module["_chain_id_checks"](data, report)
    assert report.errors == []

    mismatched = {
        **data,
        "latest_final": {
            "readbacks": {
                "get_policy": {"result": {"deployment_chain_id": 1}},
                "get_rehearsal": {"result": {"deployment_chain_id": 2}},
            }
        },
    }
    mismatch_report = evidence_module["Report"]("mismatched-genvm-readbacks")
    evidence_module["_chain_id_checks"](mismatched, mismatch_report)
    assert any("policy and rehearsal message chain IDs differ" in error for error in mismatch_report.errors)


def test_evidence_contract_addresses_keep_routable_and_genvm_domains_separate(
    evidence_module,
):
    routable = "0x" + "11" * 20
    genvm = "0x" + "22" * 20
    data = {
        "contract": {
            "address": routable,
            "genvm_address": genvm,
        },
        "latest_final": {
            "readbacks": {
                "get_policy": {
                    "result": {"deployment_contract_address": genvm.upper().replace("0X", "0x")}
                },
                "get_rehearsal": {
                    "result": {"deployment_contract_address": genvm}
                },
            }
        },
    }
    report = evidence_module["Report"]("distinct-address-domains")
    evidence_module["_address_checks"](data, report)
    assert report.errors == []

    data["latest_final"]["readbacks"]["get_rehearsal"]["result"][
        "deployment_contract_address"
    ] = "0x" + "33" * 20
    mismatch_report = evidence_module["Report"]("mismatched-genvm-addresses")
    evidence_module["_address_checks"](data, mismatch_report)
    assert any(
        "policy and rehearsal GenVM addresses differ" in error
        for error in mismatch_report.errors
    )

    schema = evidence_module["load_json"](ROOT / "deployments" / "schema.json")
    address_shape = schema["$defs"]["address"]
    malformed = evidence_module["schema_errors"](
        "0x1234", address_shape, schema, "$.contract.address"
    )
    assert malformed == ["$.contract.address: does not match the required pattern"]


def test_legacy_evidence_is_accepted_only_without_evidence_grade_requirement():
    legacy = verifier(str(LEGACY_EVIDENCE))
    assert legacy.returncode == 0, legacy.stdout
    assert "PASS [legacy-v1]" in legacy.stdout
    assert "WARNING:" in legacy.stdout

    strict = verifier(str(LEGACY_EVIDENCE), "--require-evidence-grade")
    assert strict.returncode == 1, strict.stdout
    assert "FAIL [legacy-v1]" in strict.stdout
    assert "require-evidence-grade" in strict.stdout
