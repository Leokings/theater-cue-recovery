from pathlib import Path
import json

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "theater_cue_recovery.py"
SDK = "v0.2.16"
PROMPT = "Triage a fictional or rehearsal-only theater cue incident"


def rehearsal(vm, deploy, manager):
    vm.sender = manager
    contract = deploy(str(CONTRACT), "A rehearsal-only cue sequence for testing how a small cast resumes a fictional scene after a missed transition.", sdk_version=SDK)
    contract.register_cue("C1", "ROOT", "The rehearsal reader finishes the opening line and raises a blue card.")
    contract.register_cue("C2", "C1", "The second performer crosses to the marked chair after the blue card appears.")
    contract.register_cue("C3", "C2", "The rehearsal narrator begins the closing paragraph after the chair movement ends.")
    contract.seal_cue_sheet()
    return contract


def test_triage_action_and_reporter_ack(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = rehearsal(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    contract.report_incident("I1", "During rehearsal the second performer missed the chair crossing, while no later cue had started.")
    direct_vm.mock_llm(PROMPT, json.dumps({"anchor_cue": "C2", "impact": "LOCAL"}))
    contract.triage_incident("I1")
    leader = direct_vm._captured_validators[-1][0]
    assert direct_vm.run_validator(leader_result=leader) is True
    direct_vm.sender = direct_alice
    contract.choose_recovery("I1", "REPEAT_CUE")
    direct_vm.sender = direct_bob
    contract.acknowledge_recovery("I1")
    assert contract.get_incident("I1")["acknowledged"] is True


def test_only_manager_registers_cues(direct_vm, direct_deploy, direct_alice, direct_bob):
    direct_vm.sender = direct_alice
    contract = direct_deploy(str(CONTRACT), "A rehearsal-only cue sheet for a fictional scene with no real equipment or emergency implications.", sdk_version=SDK)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("only_stage_manager"):
        contract.register_cue("C1", "ROOT", "A fictional opening cue described in enough detail for a rehearsal exercise.")


def test_model_cannot_invent_anchor(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = rehearsal(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_bob
    contract.report_incident("I1", "During rehearsal the second performer missed the chair crossing, while no later cue had started.")
    direct_vm.mock_llm(PROMPT, json.dumps({"anchor_cue": "C99", "impact": "LOCAL"}))
    with direct_vm.expect_revert("invalid_triage_value"):
        contract.triage_incident("I1")

