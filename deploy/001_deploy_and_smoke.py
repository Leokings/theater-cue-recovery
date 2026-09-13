"""Deploy V2 and record a finalized, commit-bound full-lifecycle proof.

Localnet:
    $env:THEATER_SOURCE_COMMIT = (git rev-parse HEAD)
    $env:THEATER_DEPLOY_NETWORK = "localnet"
    gltest deploy/001_deploy_and_smoke.py -v -s --network localnet

Bradbury (manager and distinct reporter must both be funded):
    $env:THEATER_SOURCE_COMMIT = (git rev-parse HEAD)
    $env:THEATER_DEPLOY_NETWORK = "testnet_bradbury"
    gltest deploy/001_deploy_and_smoke.py -v -s --network testnet_bradbury

Set THEATER_RESUME=1 to continue a clean checkpoint. If interruption leaves an
operation pending, independently prove no transaction can still finalize before
also setting THEATER_RESUME_SUBMIT=1. Bradbury's optional estimator workaround
is enabled by THEATER_BRADBURY_DEPLOY_GAS_OVERRIDE=1 and is scoped to deployment.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, cast
from urllib.parse import urlsplit, urlunsplit

from genlayer_py import create_account
from gltest import get_contract_factory, get_validator_factory
from gltest.assertions import tx_execution_succeeded
from gltest.clients import get_gl_client
from gltest.types import TransactionHashVariant, TransactionStatus
from gltest.utils import extract_contract_address
from gltest_cli.config.general import get_general_config


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "theater_cue_recovery.py"
ABI = ROOT / "abi.json"
HARNESS = Path(__file__).resolve()
REPOSITORY = "https://github.com/Leokings/theater-cue-recovery"
ALLOWED_NETWORKS = {"localnet", "testnet_bradbury"}
PORTABLE_INPUT_LIMIT = 50_000
DEFAULT_BRADBURY_GAS = 60_000_000

REHEARSAL_ID = "REHEARSAL-DEMO"
SCOPE = (
    "A fictional indoor rehearsal of three ordered dramatic cues; it excludes "
    "live safety, emergency, equipment, and workplace decisions."
)
CUES = [
    ("CUE-OPEN", "The narrator reads the opening line and raises a blue rehearsal card."),
    ("CUE-CROSS", "After the blue card appears, the second performer crosses to the marked chair."),
    ("CUE-CLOSE", "Only after the crossing ends, the narrator reads the final rehearsal line."),
]
REFERENCE = "DEPLOY-SMOKE-001"
REPORT = (
    "During this fictional rehearsal, the second performer missed only CUE-CROSS: "
    "the crossing to the marked chair did not occur after the blue card. CUE-CLOSE "
    "had not started, and no other cue was affected."
)
INCIDENT = "INCIDENT-0001"
EXPECTED = {
    "decision_status": "MATCHED",
    "anchor_index": 1,
    "anchor_cue": "CUE-CROSS",
    "impact": "LOCAL",
    "allowed_actions_json": '["REPEAT_CUE"]',
    "summary": "MATCHED|CUE-CROSS|LOCAL",
}
STEPS = [
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if hasattr(value, "to_dict"):
        return _safe(value.to_dict())
    if hasattr(value, "value"):
        return _safe(value.value)
    if hasattr(value, "__dict__"):
        return _safe(vars(value))
    return str(value)


def _write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _output(network: str) -> Path:
    name = os.environ.get(
        "THEATER_DEPLOY_OUTPUT", f"deployments/{network}-v2-smoke.local.json"
    )
    path = (ROOT / name).resolve()
    if path.parent != (ROOT / "deployments").resolve() or path.suffix.lower() != ".json":
        raise AssertionError(
            "THEATER_DEPLOY_OUTPUT must be a JSON file directly in deployments/"
        )
    return path


def _source_commit() -> str:
    value = os.environ.get("THEATER_SOURCE_COMMIT", "")
    if len(value) not in (40, 64) or value.lower() != value or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise AssertionError(
            "THEATER_SOURCE_COMMIT must be a full lowercase hexadecimal Git commit"
        )
    return value


def _file_proof(path: Path, commit: str) -> dict[str, Any]:
    relative = path.relative_to(ROOT).as_posix()
    working_raw = path.read_bytes()
    committed_raw = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    try:
        working = working_raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        committed = committed_raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    except UnicodeDecodeError as error:
        raise AssertionError(f"{relative} is not valid UTF-8 text") from error
    if working != committed:
        raise AssertionError(f"{relative} does not exactly match THEATER_SOURCE_COMMIT")
    blob = subprocess.run(
        ["git", "rev-parse", f"{commit}:{relative}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip().lower()
    return {
        "path": relative,
        "canonicalization": "UTF8_LF",
        "bytes": len(working),
        "sha256": _sha(working),
        "working_tree_bytes": len(working_raw),
        "working_tree_sha256": _sha(working_raw),
        "git_blob_id": blob,
    }


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "UNAVAILABLE"


def _public_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AssertionError("network URL contains non-public components")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AssertionError("network URL is not absolute HTTP(S)")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _network_record(general: Any) -> dict[str, Any]:
    chain = general.get_chain()
    network_name = general.get_network_name()
    configured_chain_id = int(chain.id)
    response = _safe(get_gl_client().provider.make_request("eth_chainId", []))
    result = response.get("result") if isinstance(response, dict) else None
    if isinstance(result, str):
        live_chain_id = int(result, 16) if result.startswith("0x") else int(result)
    elif isinstance(result, int):
        live_chain_id = result
    else:
        raise AssertionError(f"unsupported eth_chainId response: {response!r}")
    matches_config = live_chain_id == configured_chain_id
    equality_required = network_name == "testnet_bradbury"
    if equality_required and not matches_config:
        raise AssertionError("Bradbury outer RPC chain ID differs from configuration")
    return {
        "name": network_name,
        "chain_name": str(chain.name),
        "configured_chain_id": configured_chain_id,
        "outer_rpc_chain_id": live_chain_id,
        "outer_rpc_chain_id_verified": True,
        "outer_rpc_chain_id_matches_config": matches_config,
        "outer_rpc_equality_required": equality_required,
        "genvm_chain_id": None,
        "genvm_chain_id_source": "PENDING_DEPLOYMENT_READBACK",
        "genvm_chain_id_verified_by_readbacks": False,
        "rpc": _public_url(str(chain.rpc_urls["default"]["http"][0])),
        "explorer": _public_url(str(chain.block_explorers["default"]["url"])),
        "initial_validator_count": int(chain.default_number_of_initial_validators),
    }


def _accounts(network: str) -> tuple[Any, Any]:
    def from_environment(name: str) -> Any:
        key = os.environ.get(name, "").strip()
        raw = key[2:] if key.startswith("0x") else key
        if len(raw) != 64 or any(char.lower() not in "0123456789abcdef" for char in raw):
            raise AssertionError(f"{name} is not a 32-byte hex key")
        return create_account(bytes.fromhex(raw))

    if network == "testnet_bradbury":
        manager = from_environment("BRADBURY_GLTEST_PRIVATE_KEY")
        reporter = from_environment("THEATER_REPORTER_PRIVATE_KEY")
    else:
        manager = create_account(
            hashlib.sha256(b"theater-v2-local-manager-only").digest()
        )
        reporter = create_account(
            hashlib.sha256(b"theater-v2-local-reporter-only").digest()
        )
    if str(manager.address).lower() == str(reporter.address).lower():
        raise AssertionError("manager and reporter must be distinct")
    return manager, reporter


def _transaction_ids(value: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    names = {"transaction_hash", "transaction_id", "tx_hash", "tx_id", "hash"}

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                if str(key).lower() in names and isinstance(child, str):
                    candidate = {"path": child_path, "value": child.lower()}
                    if child.startswith("0x") and len(child) == 66 and candidate not in found:
                        found.append(candidate)
                visit(child, child_path)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")

    visit(value, "")
    return found


def _consensus(value: Any, path: str = "receipt") -> dict[str, Any]:
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in {
                "vote",
                "votes",
                "validators",
                "validator_addresses",
                "validator_receipts",
            } and child not in (None, "", [], {}):
                found[child_path] = _safe(child)
            found.update(_consensus(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(_consensus(child, f"{path}[{index}]"))
    return found


def _redact_receipt(value: Any, path: str = "receipt") -> tuple[Any, list[str]]:
    """Remove credentials without retaining hashes of individual low-entropy secrets."""
    sensitive_names = {
        "privatekey",
        "mnemonic",
        "secret",
        "password",
        "apikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
    }
    redactions: list[str] = []

    def visit(item: Any, current: str) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                child_path = f"{current}.{key}"
                normalized = "".join(char for char in str(key).lower() if char.isalnum())
                if (
                    normalized in sensitive_names
                    or normalized.endswith(("privatekey", "apikey", "accesstoken", "refreshtoken"))
                    or any(token in normalized for token in ("secret", "mnemonic", "password"))
                ):
                    result[str(key)] = "[REDACTED]"
                    redactions.append(child_path)
                else:
                    result[str(key)] = visit(child, child_path)
            return result
        if isinstance(item, list):
            return [visit(child, f"{current}[{index}]") for index, child in enumerate(item)]
        return item

    return visit(value, path), redactions


def _receipt_proof(receipt: Any, label: str) -> dict[str, Any]:
    safe = _safe(receipt)
    if not isinstance(safe, dict):
        raise AssertionError(f"{label} receipt is not an object")
    status = str(safe.get("status_name", safe.get("status", ""))).upper()
    if status != "FINALIZED" and not status.endswith(".FINALIZED"):
        raise AssertionError(f"{label} is not explicitly FINALIZED")
    if not tx_execution_succeeded(cast(Any, safe)):
        raise AssertionError(f"{label} finalized with failed GenVM execution")
    unredacted_sha256 = _sha(_canonical(safe).encode("utf-8"))
    redacted, redaction_paths = _redact_receipt(safe)
    identifiers = _transaction_ids(redacted)
    if not identifiers:
        raise AssertionError(f"{label} receipt has no transaction identifier")
    return {
        "receipt": redacted,
        "unredacted_receipt_sha256": unredacted_sha256,
        "redacted_receipt_sha256": _sha(_canonical(redacted).encode("utf-8")),
        "redaction_paths": redaction_paths,
        "redaction_policy": "RECURSIVE_CREDENTIAL_KEYS_V1",
        "transaction_identifiers": identifiers,
        "consensus_evidence": _consensus(redacted),
    }


def _verify_stored_receipt(proof: dict[str, Any], label: str) -> None:
    receipt = proof.get("receipt")
    if not isinstance(receipt, dict):
        raise AssertionError(f"stored {label} receipt is not an object")
    status = str(receipt.get("status_name", receipt.get("status", ""))).upper()
    if status != "FINALIZED" and not status.endswith(".FINALIZED"):
        raise AssertionError(f"stored {label} receipt is not FINALIZED")
    if not tx_execution_succeeded(cast(Any, receipt)):
        raise AssertionError(f"stored {label} receipt does not prove successful execution")
    digest = proof.get("unredacted_receipt_sha256", "")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        char not in "0123456789abcdef" for char in digest
    ):
        raise AssertionError(f"stored {label} unredacted receipt commitment is invalid")
    if proof.get("redacted_receipt_sha256") != _sha(_canonical(receipt).encode("utf-8")):
        raise AssertionError(f"stored {label} redacted receipt commitment differs")
    redacted_again, paths = _redact_receipt(receipt)
    if redacted_again != receipt or paths != proof.get("redaction_paths"):
        raise AssertionError(f"stored {label} receipt redactions are not reproducible")
    if proof.get("redaction_policy") != "RECURSIVE_CREDENTIAL_KEYS_V1":
        raise AssertionError(f"stored {label} receipt uses an unknown redaction policy")
    if not isinstance(proof.get("redaction_paths"), list):
        raise AssertionError(f"stored {label} redaction paths are malformed")
    if proof.get("transaction_identifiers") != _transaction_ids(receipt):
        raise AssertionError(f"stored {label} transaction identifiers differ")
    if proof.get("consensus_evidence") != _consensus(receipt):
        raise AssertionError(f"stored {label} consensus extraction differs")
    intent = proof.get("submission_intent")
    if not isinstance(intent, dict) or intent.get("label") != label:
        raise AssertionError(f"stored {label} submission intent is missing")
    if intent.get("args_sha256") != _sha(_canonical(intent.get("args")).encode("utf-8")):
        raise AssertionError(f"stored {label} argument commitment differs")
    if not isinstance(proof.get("finalized_recorded_at"), str):
        raise AssertionError(f"stored {label} finalization capture time is missing")


def _has_vote_evidence(value: dict[str, Any]) -> bool:
    paths = [path.lower() for path in value]
    return any(".vote" in path for path in paths) and any(
        ".validators" in path
        or ".validator_addresses" in path
        or ".validator_receipts" in path
        for path in paths
    )


def _deploy(
    factory: Any, args: list[Any], account: Any, network: str
) -> tuple[Any, dict[str, Any]]:
    override = os.environ.get("THEATER_BRADBURY_DEPLOY_GAS_OVERRIDE") == "1"
    if override and network != "testnet_bradbury":
        raise AssertionError("Bradbury gas override cannot be used on another network")
    if not override:
        receipt = factory.deploy_contract_tx(
            args=args,
            account=account,
            wait_transaction_status=TransactionStatus.FINALIZED,
        )
        return receipt, {"applied": False, "scope": "NONE", "limit": None}
    limit = int(
        os.environ.get("THEATER_BRADBURY_DEPLOY_GAS_LIMIT", str(DEFAULT_BRADBURY_GAS))
    )
    if not 1_000_000 <= limit <= 100_000_000:
        raise AssertionError("Bradbury deployment gas limit must be 1,000,000..100,000,000")
    client = get_gl_client()
    original = client.provider.make_request

    def scoped(method: str, params: Any = None) -> Any:
        if method == "eth_estimateGas":
            return {"jsonrpc": "2.0", "id": 0, "result": hex(limit)}
        return original(method, params=params)

    client.provider.make_request = scoped
    try:
        receipt = factory.deploy_contract_tx(
            args=args,
            account=account,
            wait_transaction_status=TransactionStatus.FINALIZED,
        )
    finally:
        client.provider.make_request = original
    return receipt, {
        "applied": True,
        "scope": "DEPLOYMENT_ETH_ESTIMATEGAS_ONLY",
        "limit": limit,
    }


def _triage_context(network: str) -> tuple[Any, str]:
    mocked = os.environ.get("THEATER_LOCAL_MOCK_CONSENSUS") == "1"
    if mocked and network != "localnet":
        raise AssertionError("mock consensus is restricted to localnet")
    if not mocked:
        return None, "NETWORK_SELECTED_REAL_INFERENCE"
    responses = {
        "nondet_exec_prompt": {
            "Classify one fictional or rehearsal-only theater cue incident": _canonical(
                {"status": "MATCHED", "anchor_index": 1, "impact": "LOCAL"}
            ),
            "Audit a proposed theater cue classification": _canonical({"accept": True}),
        }
    }
    validators = get_validator_factory().batch_create_mock_validators(
        5, mock_llm_response=cast(Any, responses)
    )
    return {"validators": [item.to_dict() for item in validators]}, "FIVE_VALIDATOR_LOCAL_MOCK"


def _latest(method: Any, args: list[Any] | None = None) -> Any:
    return _safe(
        method(args=args or []).call(
            transaction_hash_variant=TransactionHashVariant.LATEST_FINAL
        )
    )


def _all_readbacks(contract: Any, manager: str, reporter: str) -> dict[str, Any]:
    policy = _latest(contract.get_policy)
    rehearsal = _latest(contract.get_rehearsal)
    cues = [
        _latest(contract.get_cue_at, [index])
        for index in range(int(rehearsal["cue_count"]))
    ]
    reporters = [
        _latest(contract.get_reporter_at, [index])
        for index in range(int(rehearsal["reporter_count"]))
    ]
    incidents = [
        _latest(contract.get_incident_at, [index])
        for index in range(int(rehearsal["incident_count"]))
    ]
    return {
        "transaction_hash_variant": "LATEST_FINAL",
        "policy": policy,
        "rehearsal": rehearsal,
        "cues_by_index": cues,
        "cues_by_id": {
            item["cue_id"]: _latest(contract.get_cue, [item["cue_id"]])
            for item in cues
        },
        "reporters_by_index": reporters,
        "reporters_by_address": {
            manager: _latest(contract.get_reporter, [manager]),
            reporter: _latest(contract.get_reporter, [reporter]),
        },
        "incidents_by_index": incidents,
        "incidents_by_id": {
            item["record_id"]: _latest(contract.get_incident, [item["record_id"]])
            for item in incidents
        },
    }


def _readback_hash(readbacks: dict[str, Any]) -> str:
    return _sha(_canonical(readbacks).encode("utf-8"))


def _bind_genvm_chain_domain(
    record: dict[str, Any], readbacks: dict[str, Any]
) -> int:
    policy_chain_id = int(readbacks["policy"]["deployment_chain_id"])
    rehearsal_chain_id = int(readbacks["rehearsal"]["deployment_chain_id"])
    if policy_chain_id < 1 or policy_chain_id != rehearsal_chain_id:
        raise AssertionError("policy and rehearsal GenVM chain IDs are invalid or inconsistent")
    existing = record.get("genvm_chain_id")
    if existing is not None and int(existing) != policy_chain_id:
        raise AssertionError("GenVM chain ID changed after the deployment checkpoint")
    record["genvm_chain_id"] = policy_chain_id
    record["network"]["genvm_chain_id"] = policy_chain_id
    record["network"]["genvm_chain_id_source"] = (
        "get_policy.deployment_chain_id|get_rehearsal.deployment_chain_id"
    )
    record["network"]["genvm_chain_id_verified_by_readbacks"] = True
    return policy_chain_id


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise AssertionError(f"{label} is not a lowercase 32-byte digest")
    return value


def _assert_progress(completed: int, views: dict[str, Any], reporter: str) -> None:
    rehearsal = views["rehearsal"]
    expected_reporters = 2 if completed >= 1 else 1
    expected_cues = min(max(completed - 1, 0), 3)
    if int(rehearsal["reporter_count"]) != expected_reporters:
        raise AssertionError("unexpected reporter count at checkpoint")
    if int(rehearsal["cue_count"]) != expected_cues:
        raise AssertionError("unexpected cue count at checkpoint")
    if rehearsal["mode"] != ("REHEARSAL" if completed >= 5 else "CUE_SHEET"):
        raise AssertionError("unexpected mode at checkpoint")
    if int(rehearsal["incident_count"]) != (1 if completed >= 6 else 0):
        raise AssertionError("unexpected incident count at checkpoint")
    authorized = views["reporters_by_address"][reporter]["authorized"]
    if bool(authorized) != (completed >= 1):
        raise AssertionError("reporter authorization differs from checkpoint")
    if completed < 6:
        return
    incident = views["incidents_by_id"][INCIDENT]
    workflows = {
        6: "REPORTED",
        7: "TRIAGED",
        8: "ACTION_SELECTED",
        9: "ACKNOWLEDGED",
    }
    if incident["workflow"] != workflows[completed]:
        raise AssertionError("incident workflow differs from checkpoint")
    if completed >= 7:
        for field, expected in EXPECTED.items():
            if incident[field] != expected:
                raise AssertionError(f"exact semantic smoke mismatch: {field}")
    if completed >= 8 and (
        incident["action"], incident["action_target"]
    ) != ("REPEAT_CUE", "CUE-CROSS"):
        raise AssertionError("deterministic recovery selection differs from fixture")
    if completed == 9 and incident["acknowledged"] is not True:
        raise AssertionError("reporter acknowledgement was not finalized")


def _assert_final(
    views: dict[str, Any], manager: str, reporter: str, address: str, genvm_chain_id: int
) -> dict[str, str]:
    _assert_progress(9, views, reporter)
    policy, rehearsal = views["policy"], views["rehearsal"]
    if policy["contract_version"] != "2.0.0":
        raise AssertionError("unexpected contract version")
    if policy["policy_version"] != "THEATER_CUE_RECOVERY_V2":
        raise AssertionError("unexpected policy version")
    if int(policy["deployment_chain_id"]) != genvm_chain_id:
        raise AssertionError("policy GenVM chain binding differs from proof")
    if int(rehearsal["deployment_chain_id"]) != genvm_chain_id:
        raise AssertionError("rehearsal GenVM chain binding differs from proof")
    if str(policy["deployment_contract_address"]).lower() != address:
        raise AssertionError("contract self-address differs from proof")
    if str(policy["stage_manager"]).lower() != manager:
        raise AssertionError("manager differs from deployment signer")
    if policy["rehearsal_id"] != REHEARSAL_ID or policy["rehearsal_scope"] != SCOPE:
        raise AssertionError("constructor readback differs from fixture")
    if policy["source_mode"] != "ONCHAIN_TEXT_ONLY" or policy["external_sources"] is not False:
        raise AssertionError("source policy differs from V2")
    for index, actual in enumerate(views["cues_by_index"]):
        cue_id, description = CUES[index]
        expected = {
            "cue_id": cue_id,
            "index": index,
            "predecessor": "ROOT" if index == 0 else CUES[index - 1][0],
            "successor": CUES[index + 1][0] if index < 2 else "",
            "description": description,
            "cue_sheet_digest": rehearsal["cue_sheet_digest"],
        }
        if actual != expected or views["cues_by_id"][cue_id] != expected:
            raise AssertionError(f"cue readback differs: {cue_id}")
    sheet = [
        {key: cue[key] for key in ("index", "cue_id", "predecessor", "successor", "description")}
        for cue in views["cues_by_index"]
    ]
    if json.loads(rehearsal["cue_sheet_json"]) != sheet:
        raise AssertionError("cue-sheet JSON differs from indexed readbacks")
    incident = views["incidents_by_id"][INCIDENT]
    fixed = {
        "record_id": INCIDENT,
        "client_reference": REFERENCE,
        "report": REPORT,
        "reporter": reporter,
        "action": "REPEAT_CUE",
        "action_target": "CUE-CROSS",
        "workflow": "ACKNOWLEDGED",
        "policy_version": "THEATER_CUE_RECOVERY_V2",
        "source_mode": "ONCHAIN_TEXT_ONLY",
    }
    for field, expected in fixed.items():
        actual = str(incident[field]).lower() if field == "reporter" else incident[field]
        if actual != expected:
            raise AssertionError(f"incident readback differs: {field}")
    if int(rehearsal["open_incidents"]) != 0 or int(rehearsal["resolved_incidents"]) != 1:
        raise AssertionError("final incident counters are inconsistent")
    lineage = {
        field: _digest(incident[field], field)
        for field in (
            "reporter_policy_digest",
            "cue_sheet_digest",
            "config_digest",
            "reference_digest",
            "report_digest",
            "submission_digest",
            "request_digest",
            "creation_digest",
            "decision_digest",
            "action_digest",
            "final_digest",
            "record_digest",
        )
    }
    if lineage["record_digest"] != lineage["final_digest"]:
        raise AssertionError("record digest does not terminate at final digest")
    if lineage["config_digest"] != policy["config_digest"]:
        raise AssertionError("incident config digest differs from policy")
    if lineage["cue_sheet_digest"] != policy["cue_sheet_digest"]:
        raise AssertionError("incident cue-sheet digest differs from policy")
    return lineage


def _abi_preflight() -> None:
    schema = json.loads(ABI.read_text(encoding="utf-8"))
    constructor = [["rehearsal_id", "string"], ["rehearsal_scope", "string"]]
    if schema.get("ctor", {}).get("params") != constructor:
        raise AssertionError("abi.json constructor is stale")
    expected = {
        "set_reporter",
        "register_cue",
        "seal_cue_sheet",
        "report_incident",
        "triage_incident",
        "choose_recovery",
        "acknowledge_recovery",
        "cancel_incident",
        "get_policy",
        "get_rehearsal",
        "get_cue",
        "get_cue_at",
        "get_reporter",
        "get_reporter_at",
        "get_incident",
        "get_incident_at",
    }
    if set(schema.get("methods", {})) != expected:
        raise AssertionError("abi.json method set is stale")


def _begin(
    record: dict[str, Any], path: Path, label: str, sender: str, args: list[Any]
) -> None:
    record["pending_operation"] = {
        "label": label,
        "sender": sender,
        "args": _safe(args),
        "args_sha256": _sha(_canonical(_safe(args)).encode("utf-8")),
        "recorded_at": _now(),
    }
    record["record_status"] = f"{label.upper()}_SUBMISSION_INTENT_RECORDED"
    record["updated_at"] = _now()
    _write(path, record)


def _clear_pending(
    record: dict[str, Any],
    path: Path,
    contract: Any | None,
    manager: str,
    reporter: str,
) -> None:
    pending = record.get("pending_operation")
    if not pending:
        return
    if pending["label"] in record["transactions"]:
        record["pending_operation"] = None
        _write(path, record)
        return
    if os.environ.get("THEATER_RESUME_SUBMIT") != "1":
        raise AssertionError(
            f"operation {pending['label']!r} is uncertain; inspect the network before opting into resubmission"
        )
    if contract is not None:
        live = _all_readbacks(contract, manager, reporter)
        _bind_genvm_chain_domain(record, live)
        if _readback_hash(live) != record["latest_final_readbacks_sha256"]:
            raise AssertionError("LATEST_FINAL changed after uncertain submission")
    record.setdefault("operator_resume_assertions", []).append(
        {
            "operation": pending["label"],
            "assertion": "NO_OUTSTANDING_TRANSACTION_CONFIRMED_BEFORE_RESUBMIT",
            "recorded_at": _now(),
        }
    )
    record["pending_operation"] = None
    _write(path, record)


def _run_step(
    record: dict[str, Any],
    path: Path,
    index: int,
    label: str,
    sender: str,
    args: list[Any],
    submit: Callable[[], Any],
    contract: Any,
    manager: str,
    reporter: str,
) -> None:
    completed = int(record["completed_steps"])
    if completed > index:
        if label not in record["transactions"]:
            raise AssertionError(f"checkpoint omits {label} receipt")
        return
    if completed != index:
        raise AssertionError("checkpoint step index is inconsistent")
    if label not in record["transactions"]:
        _clear_pending(record, path, contract, manager, reporter)
        before = _all_readbacks(contract, manager, reporter)
        _bind_genvm_chain_domain(record, before)
        if _readback_hash(before) != record["latest_final_readbacks_sha256"]:
            raise AssertionError(f"LATEST_FINAL changed before {label}")
        _begin(record, path, label, sender, args)
        try:
            result = submit()
        except Exception as error:
            record["record_status"] = f"{label.upper()}_SUBMISSION_UNCERTAIN"
            record["last_error"] = {
                "type": type(error).__name__,
                "message_sha256": _sha(str(error).encode("utf-8")),
            }
            _write(path, record)
            raise
        proof = _receipt_proof(result, label)
        proof["submission_intent"] = record["pending_operation"]
        proof["finalized_recorded_at"] = _now()
        record["transactions"][label] = proof
        record["pending_operation"] = None
        record["last_error"] = None
        record["record_status"] = f"{label.upper()}_FINALIZED_READBACK_PENDING"
        _write(path, record)
    views = _all_readbacks(contract, manager, reporter)
    _bind_genvm_chain_domain(record, views)
    _assert_progress(index + 1, views, reporter)
    record["completed_steps"] = index + 1
    record["checkpoint_stage"] = label
    record["latest_final_readbacks_sha256"] = _readback_hash(views)
    record["latest_final_checkpoint"] = {
        "rehearsal": views["rehearsal"],
        "incident": views["incidents_by_id"].get(INCIDENT, {}),
    }
    record["record_status"] = f"{label.upper()}_FINALIZED"
    record["updated_at"] = _now()
    _write(path, record)


def test_deploy_and_smoke_finalized() -> None:
    general = get_general_config()
    network_name = general.get_network_name()
    configured = os.environ.get("THEATER_DEPLOY_NETWORK", network_name)
    if configured != network_name or network_name not in ALLOWED_NETWORKS:
        raise AssertionError("selected network must match localnet or testnet_bradbury")
    source_commit = _source_commit()
    _abi_preflight()
    network = _network_record(general)
    manager_account, reporter_account = _accounts(network_name)
    triage_context, inference_mode = _triage_context(network_name)
    manager = str(manager_account.address).lower()
    reporter = str(reporter_account.address).lower()
    source = _file_proof(CONTRACT, source_commit)
    abi = _file_proof(ABI, source_commit)
    harness = _file_proof(HARNESS, source_commit)
    source_text = CONTRACT.read_text(encoding="utf-8")
    if not source_text.splitlines()[0].startswith('# { "Depends": "py-genlayer:'):
        raise AssertionError("contract runner is not pinned")
    if "py-genlayer:test" in source_text or "py-genlayer:latest" in source_text:
        raise AssertionError("contract runner is local-only or floating")
    constructor_args = [REHEARSAL_ID, SCOPE]
    constructor_json = _canonical(constructor_args)
    constructor = {
        "args": constructor_args,
        "canonical_json": constructor_json,
        "bytes": len(constructor_json.encode()),
        "sha256": _sha(constructor_json.encode()),
    }
    if source["bytes"] + constructor["bytes"] >= PORTABLE_INPUT_LIMIT:
        raise AssertionError("deployment input exceeds the portable ceiling")
    identity = {
        "project": "theater-cue-recovery",
        "repository": REPOSITORY,
        "network_name": network_name,
        "configured_chain_id": network["configured_chain_id"],
        "outer_rpc_chain_id": network["outer_rpc_chain_id"],
        "source_commit": source_commit,
        "source_sha256": source["sha256"],
        "abi_sha256": abi["sha256"],
        "harness_sha256": harness["sha256"],
        "constructor_args_sha256": constructor["sha256"],
        "manager": manager,
        "reporter": reporter,
        "smoke_inference_mode": inference_mode,
    }
    path = _output(network_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    factory = get_contract_factory(contract_file_path=CONTRACT)
    if path.exists():
        if os.environ.get("THEATER_RESUME") != "1":
            raise AssertionError("checkpoint exists; set THEATER_RESUME=1")
        record = json.loads(path.read_text(encoding="utf-8"))
        for field, expected in identity.items():
            if record.get(field) != expected:
                raise AssertionError(f"checkpoint identity mismatch: {field}")
    else:
        record = {
            "schema_version": 2,
            **identity,
            "network": network,
            "source": source,
            "abi": abi,
            "harness": harness,
            "constructor": constructor,
            "deployment_input_bytes": source["bytes"] + constructor["bytes"],
            "contract_address": "",
            "genvm_chain_id": None,
            "completed_steps": 0,
            "checkpoint_stage": "PRE_DEPLOY",
            "pending_operation": None,
            "transactions": {},
            "latest_final_readbacks_sha256": "",
            "latest_final_checkpoint": {},
            "latest_final_readbacks": {},
            "digest_lineage": {},
            "operator_resume_assertions": [],
            "record_status": "PRE_DEPLOY",
            "last_error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "versions": {
                "contract": "2.0.0",
                "policy": "THEATER_CUE_RECOVERY_V2",
                "decision_schema": "status|anchor_index|impact",
                "validator_schema": "accept",
                "runner_dependency": source_text.splitlines()[0],
                "genlayer_py": _package_version("genlayer-py"),
                "genlayer_test": _package_version("genlayer-test"),
                "smoke_inference_mode": inference_mode,
            },
            "fixture": {
                "rehearsal_id": REHEARSAL_ID,
                "rehearsal_scope": SCOPE,
                "cues": [
                    {"cue_id": cue_id, "description": description}
                    for cue_id, description in CUES
                ],
                "client_reference": REFERENCE,
                "incident_report": REPORT,
                "record_id": INCIDENT,
                "expected_decision": EXPECTED,
                "selected_action": "REPEAT_CUE",
                "expected_action_target": "CUE-CROSS",
            },
            "limitations": [
                "Rehearsal-only: never use for live safety, emergencies, equipment control, or workplace decisions.",
                "The reporter's on-chain text is an assertion, not proof that an incident occurred.",
                "Semantic consensus can share correlated model errors and remains subject to appeals/finality.",
                "The cue sheet is a bounded linear sequence, not a branching stage-control graph.",
                "The manager records an action; the contract operates no stage system and executes no funds.",
                "This proof covers one deliberately unambiguous fixture, not every possible report.",
                "The adjudicator uses on-chain text and makes no external-source authenticity claim.",
            ],
        }
        _write(path, record)

    if not record["contract_address"]:
        _clear_pending(record, path, None, manager, reporter)
        _begin(record, path, "deployment", manager, constructor_args)
        try:
            deployed, gas_override = _deploy(
                factory, constructor_args, manager_account, network_name
            )
        except Exception as error:
            record["record_status"] = "DEPLOYMENT_SUBMISSION_UNCERTAIN"
            record["last_error"] = {
                "type": type(error).__name__,
                "message_sha256": _sha(str(error).encode("utf-8")),
            }
            _write(path, record)
            raise
        deployment_proof = _receipt_proof(deployed, "deployment")
        deployment_proof["submission_intent"] = record["pending_operation"]
        deployment_proof["finalized_recorded_at"] = _now()
        record["transactions"]["deployment"] = deployment_proof
        record["contract_address"] = str(extract_contract_address(deployed)).lower()
        record["deployment_gas_override"] = gas_override
        record["pending_operation"] = None
        record["record_status"] = "DEPLOYMENT_FINALIZED_READBACK_PENDING"
        _write(path, record)
    elif "deployment" not in record["transactions"]:
        raise AssertionError("contract address exists without a deployment receipt")

    contract: Any = factory.build_contract(
        record["contract_address"], account=manager_account
    )
    reporter_contract: Any = contract.connect(reporter_account)
    if not record["latest_final_readbacks_sha256"]:
        views = _all_readbacks(contract, manager, reporter)
        _bind_genvm_chain_domain(record, views)
        _assert_progress(0, views, reporter)
        record["latest_final_readbacks_sha256"] = _readback_hash(views)
        record["latest_final_checkpoint"] = {
            "rehearsal": views["rehearsal"],
            "incident": {},
        }
        record["checkpoint_stage"] = "deployment"
        record["record_status"] = "DEPLOYMENT_FINALIZED"
        _write(path, record)
    else:
        _clear_pending(record, path, contract, manager, reporter)
        completed = int(record["completed_steps"])
        finalized_next = completed < len(STEPS) and STEPS[completed] in record["transactions"]
        if not finalized_next:
            live = _all_readbacks(contract, manager, reporter)
            _bind_genvm_chain_domain(record, live)
            live_hash = _readback_hash(live)
            if live_hash != record["latest_final_readbacks_sha256"]:
                raise AssertionError("live LATEST_FINAL differs from resumable checkpoint")

    operations: list[tuple[str, str, list[Any], Callable[[], Any]]] = [
        (STEPS[0], manager, [reporter, True], lambda: contract.set_reporter(args=[reporter, True]).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[1], manager, list(CUES[0]), lambda: contract.register_cue(args=list(CUES[0])).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[2], manager, list(CUES[1]), lambda: contract.register_cue(args=list(CUES[1])).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[3], manager, list(CUES[2]), lambda: contract.register_cue(args=list(CUES[2])).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[4], manager, [], lambda: contract.seal_cue_sheet(args=[]).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[5], reporter, [REFERENCE, REPORT], lambda: reporter_contract.report_incident(args=[REFERENCE, REPORT]).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[6], reporter, [INCIDENT], lambda: reporter_contract.triage_incident(args=[INCIDENT]).transact(transaction_context=triage_context, wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[7], manager, [INCIDENT, "REPEAT_CUE"], lambda: contract.choose_recovery(args=[INCIDENT, "REPEAT_CUE"]).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
        (STEPS[8], reporter, [INCIDENT], lambda: reporter_contract.acknowledge_recovery(args=[INCIDENT]).transact(wait_transaction_status=TransactionStatus.FINALIZED)),
    ]
    for index, (label, sender, args, submit) in enumerate(operations):
        _run_step(
            record,
            path,
            index,
            label,
            sender,
            args,
            submit,
            contract,
            manager,
            reporter,
        )

    final_views = _all_readbacks(contract, manager, reporter)
    genvm_chain_id = _bind_genvm_chain_domain(record, final_views)
    lineage = _assert_final(
        final_views,
        manager,
        reporter,
        record["contract_address"],
        genvm_chain_id,
    )
    if set(record["transactions"]) != {"deployment", *STEPS}:
        raise AssertionError("proof is missing a lifecycle receipt")
    for label, proof in record["transactions"].items():
        _verify_stored_receipt(proof, label)
    for label in ("deployment", "triage_incident"):
        if not _has_vote_evidence(record["transactions"][label]["consensus_evidence"]):
            raise AssertionError(f"{label} lacks validator vote evidence")
    record["latest_final_readbacks"] = final_views
    record["latest_final_readbacks_sha256"] = _readback_hash(final_views)
    record["digest_lineage"] = lineage
    record["checkpoint_stage"] = "COMPLETE"
    record["record_status"] = "COMPLETE"
    record["updated_at"] = _now()
    _write(path, record)
    print(f"contract_address={record['contract_address']}")
    print(f"deployment_proof={path}")
