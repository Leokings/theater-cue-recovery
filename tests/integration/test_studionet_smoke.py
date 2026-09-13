"""Opt-in StudioNet lifecycle smoke for TheaterCueRecovery V2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionHashVariant, TransactionStatus
from gltest.utils import extract_contract_address


CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "theater_cue_recovery.py"
REHEARSAL_ID = "SHOW-STUDIONET-001"
SCOPE = (
    "A rehearsal-only fictional scene used to verify bounded cue recovery "
    "without live safety, equipment, workplace, or emergency authority."
)
CUES = (
    ("CUE-01", "The rehearsal reader finishes the opening line and raises a blue card."),
    ("CUE-02", "The second performer crosses to the marked chair after the blue card appears."),
    ("CUE-03", "The rehearsal narrator begins the closing paragraph after the chair movement ends."),
)
REPORT = (
    "During rehearsal the second performer missed the chair crossing after the "
    "blue card, while the closing narration had not started."
)


def ok(receipt):
    assert tx_execution_succeeded(receipt), receipt
    assert receipt.get("status_name") == TransactionStatus.FINALIZED.value
    assert receipt.get("result_name") in (None, "AGREE", "MAJORITY_AGREE")
    assert receipt.get("tx_execution_result_name") in (None, "FINISHED_WITH_RETURN")
    return receipt


def emit(address, deployed, intelligent, observed):
    print(
        "STUDIONET_RECORD="
        + json.dumps(
            {
                "address": address,
                "deploy_tx": deployed["hash"],
                "intelligent_tx": intelligent["hash"],
                "observed": observed,
            },
            sort_keys=True,
        )
    )


@pytest.mark.integration
def test_studionet_cue_recovery_full_lifecycle(default_account):
    factory = get_contract_factory(contract_file_path=CONTRACT)
    deployed = ok(
        factory.deploy_contract_tx(
            args=[REHEARSAL_ID, SCOPE],
            account=default_account,
            wait_transaction_status=TransactionStatus.FINALIZED,
        )
    )
    address = extract_contract_address(deployed)
    contract = factory.build_contract(address, account=default_account)
    for cue in CUES:
        ok(
            contract.register_cue(args=list(cue)).transact(
                wait_transaction_status=TransactionStatus.FINALIZED
            )
        )
    ok(
        contract.seal_cue_sheet(args=[]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    ok(
        contract.report_incident(args=["REPORT-STUDIONET-001", REPORT]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    intelligent = ok(
        contract.triage_incident(args=["INCIDENT-0001"]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    triaged = contract.get_incident(args=["INCIDENT-0001"]).call(
        transaction_hash_variant=TransactionHashVariant.LATEST_FINAL
    )
    assert triaged["decision_status"] in (
        "MATCHED",
        "AMBIGUOUS",
        "INSUFFICIENT_EVIDENCE",
        "OUT_OF_SCOPE",
    )
    assert len(triaged["decision_digest"]) == 64
    actions = json.loads(triaged["allowed_actions_json"])
    assert actions
    selected = actions[0]
    ok(
        contract.choose_recovery(args=["INCIDENT-0001", selected]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    ok(
        contract.acknowledge_recovery(args=["INCIDENT-0001"]).transact(
            wait_transaction_status=TransactionStatus.FINALIZED
        )
    )
    final = contract.get_incident(args=["INCIDENT-0001"]).call(
        transaction_hash_variant=TransactionHashVariant.LATEST_FINAL
    )
    rehearsal = contract.get_rehearsal(args=[]).call(
        transaction_hash_variant=TransactionHashVariant.LATEST_FINAL
    )
    assert final["workflow"] == "ACKNOWLEDGED"
    assert final["action"] == selected
    assert final["record_digest"] == final["final_digest"]
    assert rehearsal["resolved_incidents"] == 1
    assert rehearsal["open_incidents"] == 0
    emit(
        address,
        deployed,
        intelligent,
        {
            "record_id": "INCIDENT-0001",
            "status": final["decision_status"],
            "anchor_index": final["anchor_index"],
            "anchor_cue": final["anchor_cue"],
            "impact": final["impact"],
            "action": final["action"],
            "workflow": final["workflow"],
            "record_digest": final["record_digest"],
            "config_digest": rehearsal["config_digest"],
        },
    )
