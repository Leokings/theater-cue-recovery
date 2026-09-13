"""Five-validator GLSim regression tests for TheaterCueRecovery V2."""

from __future__ import annotations

import json
from pathlib import Path

from gltest import get_contract_factory, get_validator_factory
from gltest.accounts import create_accounts
from gltest.assertions import tx_execution_failed, tx_execution_succeeded
from gltest.types import TransactionHashVariant, TransactionStatus
from gltest.utils import extract_contract_address


CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "theater_cue_recovery.py"
REHEARSAL_ID = "SHOW-GLSIM-001"
SCOPE = (
    "A rehearsal-only fictional scene used to verify bounded five-validator "
    "cue-recovery consensus without live safety or emergency authority."
)
REPORT = (
    "During rehearsal the second performer missed the chair crossing after the "
    "blue card, while the closing narration had not started."
)
CUES = (
    ("CUE-01", "The rehearsal reader finishes the opening line and raises a blue card."),
    ("CUE-02", "The second performer crosses to the marked chair after the blue card appears."),
    ("CUE-03", "The rehearsal narrator begins the closing paragraph after the chair movement ends."),
)
LEADER_PROMPT = "Classify one fictional or rehearsal-only theater cue incident"
AUDIT_PROMPT = "Audit a proposed theater cue classification"
LATEST_FINAL = TransactionHashVariant.LATEST_FINAL


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validators(candidate=None, *, accept=True):
    decision = candidate or {
        "status": "MATCHED",
        "anchor_index": 1,
        "impact": "LOCAL",
    }
    items = get_validator_factory().batch_create_mock_validators(
        5,
        mock_llm_response={
            "nondet_exec_prompt": {
                LEADER_PROMPT: compact(decision),
                AUDIT_PROMPT: compact({"accept": accept}),
            }
        },
    )
    return {"validators": [item.to_dict() for item in items]}


def finalized(receipt):
    assert tx_execution_succeeded(receipt), receipt
    assert receipt.get("status_name") in (None, TransactionStatus.FINALIZED.value), receipt
    assert receipt.get("result_name") in (None, "AGREE", "MAJORITY_AGREE"), receipt
    assert receipt.get("tx_execution_result_name") in (None, "FINISHED_WITH_RETURN"), receipt
    return receipt


def deploy_configured():
    manager, reporter, outsider = create_accounts(3)
    factory = get_contract_factory(contract_file_path=CONTRACT)
    deployed = finalized(
        factory.deploy_contract_tx(
            args=[REHEARSAL_ID, SCOPE],
            account=manager,
            wait_transaction_status=TransactionStatus.FINALIZED,
        )
    )
    address = extract_contract_address(deployed)
    owner = factory.build_contract(address, account=manager)
    participant = factory.build_contract(address, account=reporter)
    stranger = factory.build_contract(address, account=outsider)
    finalized(
        owner.set_reporter(args=[reporter.address, True]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    for cue in CUES:
        finalized(
            owner.register_cue(args=list(cue)).transact(
                wait_transaction_status=TransactionStatus.FINALIZED
            )
        )
    finalized(
        owner.seal_cue_sheet(args=[]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    return owner, participant, stranger, reporter.address


def submit(participant, reference="REPORT-GLSIM-001", report=REPORT):
    return finalized(
        participant.report_incident(args=[reference, report]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )


def test_glsim_deployment_exposes_sealed_linear_policy_and_provenance():
    owner, participant, _, reporter_address = deploy_configured()
    policy = owner.get_policy(args=[]).call(transaction_hash_variant=LATEST_FINAL)
    rehearsal = owner.get_rehearsal(args=[]).call(
        transaction_hash_variant=LATEST_FINAL
    )
    reporter = participant.get_reporter(args=[reporter_address]).call(
        transaction_hash_variant=LATEST_FINAL
    )

    assert policy["contract_version"] == "2.0.0"
    assert policy["policy_version"] == "THEATER_CUE_RECOVERY_V2"
    assert policy["linear_sequence"] is True
    assert policy["max_total_per_reporter"] == 8
    assert rehearsal["rehearsal_id"] == REHEARSAL_ID
    assert rehearsal["mode"] == "REHEARSAL"
    assert rehearsal["cue_count"] == 3
    assert rehearsal["reporter_count"] == 2
    assert len(rehearsal["cue_sheet_digest"]) == 64
    assert len(rehearsal["config_digest"]) == 64
    assert policy["deployment_chain_id"] == rehearsal["deployment_chain_id"]
    assert (
        str(policy["deployment_contract_address"]).lower()
        == str(rehearsal["deployment_contract_address"]).lower()
    )
    assert reporter["authorized"] is True
    assert owner.get_cue_at(args=[1]).call(
        transaction_hash_variant=LATEST_FINAL
    )["successor"] == "CUE-03"


def test_glsim_five_validators_finalize_full_recovery_lifecycle():
    owner, participant, _, _ = deploy_configured()
    submit(participant)
    triage_receipt = finalized(
        participant.triage_incident(args=["INCIDENT-0001"]).transact(
            transaction_context=validators(),
            wait_transaction_status=TransactionStatus.FINALIZED,
        )
    )
    finalized(
        owner.choose_recovery(args=["INCIDENT-0001", "REPEAT_CUE"]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    finalized(
        participant.acknowledge_recovery(args=["INCIDENT-0001"]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )

    incident = owner.get_incident(args=["INCIDENT-0001"]).call(
        transaction_hash_variant=LATEST_FINAL
    )
    rehearsal = owner.get_rehearsal(args=[]).call(
        transaction_hash_variant=LATEST_FINAL
    )
    assert incident["decision_status"] == "MATCHED"
    assert incident["anchor_index"] == 1
    assert incident["anchor_cue"] == "CUE-02"
    assert incident["impact"] == "LOCAL"
    assert incident["action"] == "REPEAT_CUE"
    assert incident["action_target"] == "CUE-02"
    assert incident["workflow"] == "ACKNOWLEDGED"
    assert incident["acknowledged"] is True
    assert len(incident["decision_digest"]) == 64
    assert len(incident["action_digest"]) == 64
    assert incident["record_digest"] == incident["final_digest"]
    assert rehearsal["open_incidents"] == 0
    assert rehearsal["resolved_incidents"] == 1
    assert triage_receipt.get("result_name") in (None, "AGREE", "MAJORITY_AGREE")


def test_glsim_malformed_leader_output_does_not_commit_triage_state():
    owner, participant, _, _ = deploy_configured()
    submit(participant)
    before = owner.get_incident(args=["INCIDENT-0001"]).call(
        transaction_hash_variant=LATEST_FINAL
    )
    assert before["workflow"] == "REPORTED"
    receipt = participant.triage_incident(args=["INCIDENT-0001"]).transact(
        transaction_context=validators({"status": "MATCHED"}),
        wait_transaction_status=TransactionStatus.FINALIZED,
    )
    assert tx_execution_failed(receipt), receipt
    incident = owner.get_incident(args=["INCIDENT-0001"]).call(
        transaction_hash_variant=LATEST_FINAL
    )
    assert incident["workflow"] == "REPORTED"
    assert incident["decision_status"] == ""
    assert incident["decision_digest"] == ""
    assert owner.get_rehearsal(args=[]).call(
        transaction_hash_variant=LATEST_FINAL
    )["inconclusive_incidents"] == 0


def test_glsim_unauthorized_report_is_finalized_failure_without_state():
    owner, _, stranger, _ = deploy_configured()
    receipt = stranger.report_incident(
        args=["REPORT-GLSIM-BAD", REPORT]
    ).transact(wait_transaction_status=TransactionStatus.FINALIZED)
    assert tx_execution_failed(receipt), receipt
    rehearsal = owner.get_rehearsal(args=[]).call(
        transaction_hash_variant=LATEST_FINAL
    )
    assert rehearsal["incident_count"] == 0
    assert rehearsal["open_incidents"] == 0
