from pathlib import Path
import json

from gltest import get_contract_factory, get_validator_factory
from gltest.accounts import create_accounts
from gltest.assertions import tx_execution_succeeded
from gltest.types import TransactionStatus
from gltest.utils import extract_contract_address

PROMPT = "Triage a fictional or rehearsal-only theater cue incident"


def context():
    validators = get_validator_factory().batch_create_mock_validators(5, mock_llm_response={"nondet_exec_prompt": {PROMPT: json.dumps({"anchor_cue": "C2", "impact": "LOCAL"})}})
    return {"validators": [item.to_dict() for item in validators]}


def ok(receipt):
    assert tx_execution_succeeded(receipt)


def test_five_validators_triage_sealed_cue_sheet():
    manager, reporter = create_accounts(2)
    factory = get_contract_factory(contract_file_path=Path(__file__).resolve().parents[2] / "contracts" / "theater_cue_recovery.py")
    deployed = factory.deploy_contract_tx(args=["A rehearsal-only cue sequence for resuming a fictional scene after a missed transition, with no live safety role."], account=manager, wait_transaction_status=TransactionStatus.FINALIZED)
    ok(deployed)
    address = extract_contract_address(deployed)
    owner = factory.build_contract(address, account=manager)
    participant = factory.build_contract(address, account=reporter)
    for args in [("C1", "ROOT", "The reader finishes the opening line and raises a blue card."), ("C2", "C1", "The second performer crosses to a marked chair after the card appears."), ("C3", "C2", "The narrator begins the closing paragraph after the movement ends.")]:
        ok(owner.register_cue(args=list(args)).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    ok(owner.seal_cue_sheet(args=[]).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    ok(participant.report_incident(args=["I1", "During rehearsal the second performer missed the chair crossing, while no later cue had started."]).transact(wait_transaction_status=TransactionStatus.FINALIZED))
    ok(owner.triage_incident(args=["I1"]).transact(transaction_context=context(), wait_transaction_status=TransactionStatus.FINALIZED))
    assert owner.get_incident(args=["I1"]).call()["anchor_cue"] == "C2"

