import json
from pathlib import Path

import pytest
from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionHashVariant, TransactionStatus
from gltest.utils import extract_contract_address


def ok(receipt):
    assert tx_execution_succeeded(receipt)
    assert receipt.get("status_name") == TransactionStatus.FINALIZED.value
    assert receipt.get("result_name") in (None, "AGREE", "MAJORITY_AGREE")
    assert receipt.get("tx_execution_result_name") in (None, "FINISHED_WITH_RETURN")
    return receipt


def emit(address, deployed, intelligent, observed):
    print("STUDIONET_RECORD=" + json.dumps({"address": address, "deploy_tx": deployed["hash"], "intelligent_tx": intelligent["hash"], "observed": observed}, sort_keys=True))


@pytest.mark.integration
def test_studionet_cue_triage(default_account):
    factory = get_contract_factory(contract_file_path=Path(__file__).resolve().parents[2] / "contracts" / "theater_cue_recovery.py")
    deployed = ok(factory.deploy_contract_tx(args=["A rehearsal-only cue sequence for resuming a fictional scene after a missed transition, with no live safety role."], account=default_account, wait_transaction_status=TransactionStatus.FINALIZED))
    address = extract_contract_address(deployed)
    contract = factory.build_contract(address, account=default_account)
    for args in [("C1", "ROOT", "The reader finishes the opening line and raises a blue card."), ("C2", "C1", "The second performer crosses to a marked chair after the card appears."), ("C3", "C2", "The narrator begins the closing paragraph after the movement ends.")]:
        ok(contract.register_cue(args=list(args)).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    ok(contract.seal_cue_sheet(args=[]).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    ok(contract.report_incident(args=["I1", "During rehearsal the second performer missed the chair crossing, while no later cue had started."]).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    intelligent = ok(contract.triage_incident(args=["I1"]).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    result = contract.get_incident(args=["I1"]).call(transaction_hash_variant=TransactionHashVariant.LATEST_FINAL)
    assert result["anchor_cue"] in ("C1", "C2", "C3")
    assert result["impact"] in ("LOCAL", "CHAIN", "RESET")
    emit(address, deployed, intelligent, {"anchor": result["anchor_cue"], "impact": result["impact"]})

