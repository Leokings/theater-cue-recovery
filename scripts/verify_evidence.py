#!/usr/bin/env python3
"""Verify Theater Cue Recovery deployment evidence with the Python stdlib.

The JSON Schema owns field-level validation. This script implements the small
Draft 2020-12 subset used by that schema, then checks provenance, hashes,
consensus records, and cross-readback invariants. It also recognizes the
original compact StudioNet manifest as legacy evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse


EVIDENCE_SCHEMA = "theater-cue-recovery/deployment-evidence/v2"
POLICY_VERSION = "THEATER_CUE_RECOVERY_V2"
STATUSES = ("MATCHED", "AMBIGUOUS", "INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE")
IMPACTS = ("NONE", "LOCAL", "CHAIN", "RESET")
ACTIONS = (
    "REPEAT_CUE",
    "SKIP_TO_SUCCESSOR",
    "RESET_SEQUENCE",
    "REQUEST_CLARIFICATION",
    "CLOSE_NO_ACTION",
)
METHODS = {
    "deploy": "__init__",
    "set_reporter": "set_reporter",
    "register_cue": "register_cue",
    "seal_cue_sheet": "seal_cue_sheet",
    "report_incident": "report_incident",
    "triage_incident": "triage_incident",
    "choose_recovery": "choose_recovery",
    "acknowledge_recovery": "acknowledge_recovery",
    "cancel_incident": "cancel_incident",
}


class JsonError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JsonError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise JsonError(f"non-finite number {value!r} is not JSON")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=_unique_object, parse_constant=_nonfinite)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_source(value: bytes) -> bytes:
    text = value.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return text.encode("utf-8")


def canonical_document(value: bytes) -> bytes:
    parsed = json.loads(
        value.decode("utf-8-sig"),
        object_pairs_hook=_unique_object,
        parse_constant=_nonfinite,
    )
    return canonical(parsed)


@dataclass
class Report:
    source: str
    grade: str = "unknown"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    def json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "grade": self.grade,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _resolve(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise JsonError(f"unsupported non-local schema reference {reference!r}")
    value: Any = root
    for token in reference[2:].split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise JsonError(f"schema reference {reference!r} is not an object")
    return value


def _json_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _format_ok(value: str, kind: str) -> bool:
    try:
        if kind == "date":
            return date.fromisoformat(value).isoformat() == value
        if kind == "date-time":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None
        if kind == "uri":
            parsed = urlparse(value)
            return bool(parsed.scheme and (parsed.netloc or parsed.path))
    except ValueError:
        return False
    return True


def schema_errors(
    value: Any, spec: dict[str, Any], root: dict[str, Any], path: str = "$"
) -> list[str]:
    """Validate only the schema keywords used by deployments/schema.json."""
    errors: list[str] = []

    def check(item: Any, rule: dict[str, Any], at: str) -> None:
        if "$ref" in rule:
            check(item, _resolve(root, rule["$ref"]), at)
        for branch in rule.get("allOf", []):
            check(item, branch, at)
        if "oneOf" in rule:
            matches = 0
            for branch in rule["oneOf"]:
                saved = list(errors)
                errors.clear()
                check(item, branch, at)
                matches += not errors
                errors[:] = saved
            if matches != 1:
                errors.append(f"{at}: must match exactly one allowed shape")
        if "const" in rule and item != rule["const"]:
            errors.append(f"{at}: must equal {rule['const']!r}")
        if "enum" in rule and item not in rule["enum"]:
            errors.append(f"{at}: value is not in the allowed set")
        expected = rule.get("type")
        if expected and not _json_type(item, expected):
            errors.append(f"{at}: must be {expected}")
            return
        if isinstance(item, dict):
            required = set(rule.get("required", []))
            missing = sorted(required - item.keys())
            if missing:
                errors.append(f"{at}: missing {', '.join(missing)}")
            properties = rule.get("properties", {})
            if rule.get("additionalProperties") is False:
                extra = sorted(item.keys() - properties.keys())
                if extra:
                    errors.append(f"{at}: unknown keys {', '.join(extra)}")
            elif isinstance(rule.get("additionalProperties"), dict):
                for key in item.keys() - properties.keys():
                    check(item[key], rule["additionalProperties"], f"{at}.{key}")
            if len(item) < rule.get("minProperties", 0):
                errors.append(f"{at}: has too few properties")
            if "maxProperties" in rule and len(item) > rule["maxProperties"]:
                errors.append(f"{at}: has too many properties")
            for key, child in properties.items():
                if key in item:
                    check(item[key], child, f"{at}.{key}")
        if isinstance(item, list):
            if len(item) < rule.get("minItems", 0):
                errors.append(f"{at}: has too few items")
            if "maxItems" in rule and len(item) > rule["maxItems"]:
                errors.append(f"{at}: has too many items")
            prefix = rule.get("prefixItems", [])
            for index, child in enumerate(prefix[: len(item)]):
                check(item[index], child, f"{at}[{index}]")
            child = rule.get("items")
            if isinstance(child, dict):
                for index in range(len(prefix), len(item)):
                    check(item[index], child, f"{at}[{index}]")
        if isinstance(item, str):
            if len(item) < rule.get("minLength", 0):
                errors.append(f"{at}: is too short")
            if "maxLength" in rule and len(item) > rule["maxLength"]:
                errors.append(f"{at}: is too long")
            if "pattern" in rule and re.search(rule["pattern"], item) is None:
                errors.append(f"{at}: does not match the required pattern")
            if "format" in rule and not _format_ok(item, rule["format"]):
                errors.append(f"{at}: is not a valid {rule['format']}")
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if "minimum" in rule and item < rule["minimum"]:
                errors.append(f"{at}: is below the minimum")
            if "maximum" in rule and item > rule["maximum"]:
                errors.append(f"{at}: is above the maximum")

    check(value, spec, path)
    return errors


def _safe_file(root: Path, value: str) -> Path | None:
    posix = PurePosixPath(value.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts:
        return None
    candidate = (root / Path(*posix.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    command = ["git", *args]
    try:
        return subprocess.run(
            command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, b"", str(exc).encode())


def _repository_checks(data: dict[str, Any], root: Path, report: Report) -> None:
    repo = data["repository"]
    contract_path = _safe_file(root, repo["contract_path"])
    abi_path = _safe_file(root, repo["abi_path"])
    if contract_path is None or abi_path is None:
        report.error("$.repository", "contract_path and abi_path must stay inside repo root")
        return
    for path, field_name in ((contract_path, "contract_path"), (abi_path, "abi_path")):
        if not path.is_file():
            report.error(f"$.repository.{field_name}", "file does not exist")
            return
    try:
        source_hash = sha256(normalized_source(contract_path.read_bytes()))
        abi_hash = sha256(canonical_document(abi_path.read_bytes()))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        report.error("$.repository", f"cannot hash local artifacts: {exc}")
        return
    if source_hash != repo["contract_source_sha256"]:
        report.error("$.repository.contract_source_sha256", f"local digest is {source_hash}")
    if abi_hash != repo["abi_schema_sha256"]:
        report.error("$.repository.abi_schema_sha256", f"local digest is {abi_hash}")

    commit = repo["commit"]
    verified = _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if verified.returncode != 0 or verified.stdout.decode().strip().lower() != commit:
        report.error("$.repository.commit", "commit is not available in this checkout")
        return
    blob = _git(root, "rev-parse", f"{commit}:{repo['contract_path']}")
    if blob.returncode != 0:
        report.error("$.repository.contract_path", "path is absent from the pinned commit")
    elif blob.stdout.decode().strip().lower() != repo["contract_git_blob_sha1"]:
        report.error("$.repository.contract_git_blob_sha1", "does not match the pinned commit")
    transforms: tuple[tuple[str, str, Callable[[bytes], bytes]], ...] = (
        ("contract_source_sha256", "contract_path", normalized_source),
        ("abi_schema_sha256", "abi_path", canonical_document),
    )
    for digest_key, path_key, transform in transforms:
        pinned = _git(root, "show", f"{commit}:{repo[path_key]}")
        if pinned.returncode != 0:
            report.error(f"$.repository.{path_key}", "cannot read artifact at pinned commit")
            continue
        try:
            digest = sha256(transform(pinned.stdout))
        except (UnicodeError, ValueError, TypeError) as exc:
            report.error(f"$.repository.{path_key}", f"invalid pinned artifact: {exc}")
            continue
        if digest != repo[digest_key]:
            report.error(f"$.repository.{digest_key}", f"pinned-commit digest is {digest}")


def _https(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _network_url(value: str) -> bool:
    parsed = urlparse(value)
    return _https(value) or (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )


def _redact(value: Any) -> tuple[Any, list[str]]:
    """Defense-in-depth credential redaction for an embedded public capture."""
    paths: list[str] = []

    def visit(item: Any, path: str) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, child in item.items():
                child_path = f"{path}.{key}"
                name = "".join(char for char in key.lower() if char.isalnum())
                sensitive = (
                    name in {"privatekey", "mnemonic", "secret", "password", "apikey", "authorization", "accesstoken", "refreshtoken"}
                    or name.endswith(("privatekey", "apikey", "accesstoken", "refreshtoken"))
                    or any(token in name for token in ("secret", "mnemonic", "password"))
                )
                if sensitive and child != "[REDACTED]":
                    result[key] = "[REDACTED]"
                    paths.append(child_path)
                else:
                    result[key] = visit(child, child_path)
            return result
        if isinstance(item, list):
            return [visit(child, f"{path}[{index}]") for index, child in enumerate(item)]
        return item

    return visit(value, "$"), paths


def _decision_ok(value: dict[str, Any]) -> bool:
    status, index, impact = value.get("status"), value.get("anchor_index"), value.get("impact")
    if status == "MATCHED":
        return (
            isinstance(index, int)
            and not isinstance(index, bool)
            and index >= 0
            and impact in IMPACTS[1:]
        )
    return status in STATUSES and index == -1 and impact == "NONE"


def _transaction_checks(data: dict[str, Any], report: Report) -> list[dict[str, Any]]:
    txs: list[dict[str, Any]] = data["transactions"]
    address = data["deployment"]["contract_address"].lower()
    deployer = data["deployment"]["deployer"].lower()
    explorer_host = urlparse(data["network"]["explorer_url"]).netloc.lower()
    hashes: set[str] = set()
    purposes: list[str] = []
    times: list[datetime] = []
    for index, tx in enumerate(txs):
        at = f"$.transactions[{index}]"
        purpose = tx["purpose"]
        purposes.append(purpose)
        if tx["order"] != index:
            report.error(f"{at}.order", f"must equal array position {index}")
        if tx["method"] != METHODS.get(purpose):
            report.error(f"{at}.method", f"does not match purpose {purpose!r}")
        tx_hash = tx["tx_hash"].lower()
        if tx_hash in hashes:
            report.error(f"{at}.tx_hash", "duplicate transaction hash")
        hashes.add(tx_hash)
        if tx["to"].lower() != address:
            report.error(f"{at}.to", "must equal deployment contract address")
        if purpose == "deploy" and tx["from"].lower() != deployer:
            report.error(f"{at}.from", "deployment must be sent by deployer")
        link = urlparse(tx["explorer_url"])
        if not _network_url(tx["explorer_url"]) or link.netloc.lower() != explorer_host:
            report.error(f"{at}.explorer_url", "must use the declared explorer host")
        if tx_hash not in tx["explorer_url"].lower():
            report.error(f"{at}.explorer_url", "must contain its transaction hash")
        votes = tx["votes"]
        classified = votes["agree"] + votes["disagree"] + votes["idle"] + votes["other"]
        if votes["total"] != classified:
            report.error(f"{at}.votes", "total and classified vote counts disagree")
        if votes["agree"] < 1 or votes["agree"] <= votes["disagree"] + votes["other"]:
            report.error(f"{at}.votes", "record does not evidence an agreeing consensus")
        equality = tx["equality_output"]
        args_digest = sha256(canonical(tx["args"]))
        if tx["args_sha256"] != args_digest:
            report.error(f"{at}.args_sha256", f"digest mismatch; actual {args_digest}")
        if purpose == "triage_incident":
            if not isinstance(equality, dict) or set(equality) != {"status", "anchor_index", "impact"}:
                report.error(f"{at}.equality_output", "triage must record exactly the semantic decision")
            elif not _decision_ok(equality):
                report.error(f"{at}.equality_output", "semantic decision is incoherent")
        elif equality is not None:
            report.error(f"{at}.equality_output", "deterministic writes must record null")
        if tx["execution"]["stderr"] and not tx["warnings"]:
            report.error(f"{at}.warnings", "non-empty stderr requires an explicit warning")
        for note in tx["warnings"]:
            if note.get("related_tx", tx_hash).lower() != tx_hash:
                report.error(f"{at}.warnings", "related_tx must identify its enclosing transaction")
        times.append(datetime.fromisoformat(tx["created_at"].replace("Z", "+00:00")))

    if times != sorted(times):
        report.error("$.transactions", "created_at values must be chronological")
    if not purposes or purposes[0] != "deploy" or purposes.count("deploy") != 1:
        report.error("$.transactions", "must begin with exactly one deployment")
    minimums = {"register_cue": 3, "seal_cue_sheet": 1, "report_incident": 1, "triage_incident": 1}
    for purpose, minimum in minimums.items():
        if purposes.count(purpose) < minimum:
            report.error("$.transactions", f"must evidence {minimum} or more {purpose} transaction(s)")
    if purposes.count("seal_cue_sheet") != 1:
        report.error("$.transactions", "must contain exactly one cue-sheet seal")
    if "seal_cue_sheet" in purposes:
        seal = purposes.index("seal_cue_sheet")
        if any(i > seal for i, purpose in enumerate(purposes) if purpose == "register_cue"):
            report.error("$.transactions", "cue registration must precede sealing")
        if "report_incident" in purposes and seal > purposes.index("report_incident"):
            report.error("$.transactions", "sealing must precede reporting")
    if "set_reporter" in purposes and "report_incident" in purposes:
        if max(i for i, p in enumerate(purposes) if p == "set_reporter") > purposes.index("report_incident"):
            report.error("$.transactions", "reporter setup must precede reporting")
    if (
        "report_incident" in purposes
        and "triage_incident" in purposes
        and purposes.index("report_incident") > purposes.index("triage_incident")
    ):
        report.error("$.transactions", "reporting must precede triage")
    if (
        "choose_recovery" in purposes
        and "triage_incident" in purposes
        and purposes.index("choose_recovery") < purposes.index("triage_incident")
    ):
        report.error("$.transactions", "action selection must follow triage")
    if "acknowledge_recovery" in purposes and (
        "choose_recovery" not in purposes
        or purposes.index("acknowledge_recovery") < purposes.index("choose_recovery")
    ):
        report.error("$.transactions", "acknowledgement must follow action selection")
    if (
        "cancel_incident" in purposes
        and "report_incident" in purposes
        and purposes.index("cancel_incident") < purposes.index("report_incident")
    ):
        report.error("$.transactions", "cancellation must follow reporting")
    return txs


def _allowed(status: str, impact: str) -> list[str]:
    if status == "MATCHED" and impact == "LOCAL":
        return ["REPEAT_CUE"]
    if status == "MATCHED" and impact == "CHAIN":
        return ["SKIP_TO_SUCCESSOR", "RESET_SEQUENCE"]
    if status == "MATCHED" and impact == "RESET":
        return ["RESET_SEQUENCE"]
    if status in ("AMBIGUOUS", "INSUFFICIENT_EVIDENCE"):
        return ["REQUEST_CLARIFICATION", "CLOSE_NO_ACTION"]
    return ["CLOSE_NO_ACTION"]


def _final_checks(data: dict[str, Any], txs: list[dict[str, Any]], report: Report) -> None:
    latest = data["latest_final"]
    readbacks = latest["readbacks"]
    actual = sha256(canonical(readbacks))
    if latest["readbacks_sha256"] != actual:
        report.error("$.latest_final.readbacks_sha256", f"digest mismatch; actual {actual}")
    incident = readbacks["get_incident"]["result"]
    rehearsal = readbacks["get_rehearsal"]["result"]
    policy = readbacks["get_policy"]["result"]
    cues = [entry["result"] for entry in readbacks["cues"]]
    reporters = [entry["result"] for entry in readbacks["reporters"]]

    if readbacks["get_incident"]["args"] != [incident["record_id"]]:
        report.error("$.latest_final.readbacks.get_incident.args", "must identify returned record")
    for name in ("get_rehearsal", "get_policy"):
        if readbacks[name]["args"] != []:
            report.error(f"$.latest_final.readbacks.{name}.args", "must be empty")
    chain_id = data["network"]["chain_id"]
    contract = data["deployment"]["contract_address"].lower()
    deployer = data["deployment"]["deployer"].lower()
    constructor = data["deployment"]["constructor"]
    fixture = sha256(canonical({"args": constructor["args"], "kwargs": constructor["kwargs"]}))
    if constructor["fixture_sha256"] != fixture:
        report.error("$.deployment.constructor.fixture_sha256", f"digest mismatch; actual {fixture}")
    if constructor["kwargs"] or not all(isinstance(value, str) for value in constructor["args"]):
        report.error("$.deployment.constructor", "V2 requires two positional strings and no kwargs")

    for name, view in (("get_rehearsal", rehearsal), ("get_policy", policy)):
        at = f"$.latest_final.readbacks.{name}.result"
        if view["deployment_chain_id"] != chain_id:
            report.error(f"{at}.deployment_chain_id", "does not match network.chain_id")
        if view["deployment_contract_address"].lower() != contract:
            report.error(f"{at}.deployment_contract_address", "does not match deployment address")
        if view["stage_manager"].lower() != deployer:
            report.error(f"{at}.stage_manager", "does not match deployer")
        if [view["rehearsal_id"], view["rehearsal_scope"]] != constructor["args"]:
            report.error(at, "rehearsal identity does not match constructor fixture")
    shared = (
        "deployment_chain_id",
        "deployment_contract_address",
        "stage_manager",
        "rehearsal_id",
        "rehearsal_scope",
        "mode",
        "reporter_policy_version",
        "reporter_policy_digest",
        "cue_sheet_digest",
        "config_digest",
        "policy_version",
    )
    for key in shared:
        left, right = rehearsal[key], policy[key]
        if isinstance(left, str) and left.startswith("0x"):
            left, right = left.lower(), right.lower()
        if left != right:
            report.error(f"$.latest_final.readbacks.get_policy.result.{key}", "does not match rehearsal")
    for key in ("config_digest", "cue_sheet_digest", "policy_version"):
        if incident[key] != rehearsal[key]:
            report.error(f"$.latest_final.readbacks.get_incident.result.{key}", "does not match rehearsal")
    if incident["source_mode"] != policy["source_mode"]:
        report.error("$.latest_final.readbacks.get_incident.result.source_mode", "does not match policy")

    by_purpose: dict[str, list[dict[str, Any]]] = {}
    for tx in txs:
        by_purpose.setdefault(tx["purpose"], []).append(tx)
    if by_purpose.get("deploy") and by_purpose["deploy"][0]["args"] != constructor["args"]:
        report.error("$.transactions[0].args", "does not match the constructor fixture")

    if len(cues) != rehearsal["cue_count"]:
        report.error("$.latest_final.readbacks.cues", "length does not match cue_count")
    expected_sheet: list[dict[str, Any]] = []
    for index, cue in enumerate(cues):
        entry = readbacks["cues"][index]
        previous = "ROOT" if index == 0 else cues[index - 1]["cue_id"]
        following = "" if index + 1 == len(cues) else cues[index + 1]["cue_id"]
        if entry["args"] != [index] or cue["index"] != index:
            report.error(f"$.latest_final.readbacks.cues[{index}]", "index and args must be contiguous")
        if cue["predecessor"] != previous or cue["successor"] != following:
            report.error(f"$.latest_final.readbacks.cues[{index}].result", "linear links are inconsistent")
        if cue["cue_sheet_digest"] != rehearsal["cue_sheet_digest"]:
            report.error(f"$.latest_final.readbacks.cues[{index}].result.cue_sheet_digest", "digest mismatch")
        expected_sheet.append(
            {key: cue[key] for key in ("index", "cue_id", "predecessor", "successor", "description")}
        )
    if rehearsal["cue_sheet_json"] != canonical(expected_sheet).decode():
        report.error("$.latest_final.readbacks.get_rehearsal.result.cue_sheet_json", "does not match cue views")
    cue_args = [[cue["cue_id"], cue["description"]] for cue in cues]
    if [tx["args"] for tx in by_purpose.get("register_cue", [])] != cue_args:
        report.error("$.transactions", "register_cue args do not match ordered cue readbacks")

    if len(reporters) != rehearsal["reporter_count"]:
        report.error("$.latest_final.readbacks.reporters", "length does not match reporter_count")
    addresses: list[str] = []
    for index, reporter_view in enumerate(reporters):
        entry = readbacks["reporters"][index]
        address = reporter_view["reporter"].lower()
        addresses.append(address)
        if entry["args"] != [index] or not reporter_view["registered"]:
            report.error(f"$.latest_final.readbacks.reporters[{index}]", "must be a registered indexed reporter")
        if reporter_view["policy_version"] != rehearsal["reporter_policy_version"]:
            report.error(f"$.latest_final.readbacks.reporters[{index}].result.policy_version", "version mismatch")
        if reporter_view["policy_digest"] != rehearsal["reporter_policy_digest"]:
            report.error(f"$.latest_final.readbacks.reporters[{index}].result.policy_digest", "digest mismatch")
    if len(addresses) != len(set(addresses)):
        report.error("$.latest_final.readbacks.reporters", "reporter addresses must be unique")
    if not reporters or addresses[0] != deployer or not reporters[0]["authorized"]:
        report.error("$.latest_final.readbacks.reporters[0]", "first reporter must be the authorized manager")
    if incident["reporter"].lower() not in addresses:
        report.error("$.latest_final.readbacks.get_incident.result.reporter", "reporter is absent from registry")
    if len(reporters) > 1 and not any(
        len(tx["args"]) == 2
        and isinstance(tx["args"][0], str)
        and tx["args"][0].lower() == incident["reporter"].lower()
        and tx["args"][1] is True
        for tx in by_purpose.get("set_reporter", [])
    ):
        report.error("$.transactions", "no set_reporter args authorize the incident reporter")
    if sum(item["open_incidents"] for item in reporters) != rehearsal["open_incidents"]:
        report.error("$.latest_final.readbacks.reporters", "open counts do not match rehearsal")
    if sum(item["total_incidents"] for item in reporters) != rehearsal["incident_count"]:
        report.error("$.latest_final.readbacks.reporters", "total counts do not match rehearsal")
    counters = rehearsal["open_incidents"] + rehearsal["resolved_incidents"] + rehearsal["cancelled_incidents"]
    if counters != rehearsal["incident_count"]:
        report.error("$.latest_final.readbacks.get_rehearsal.result", "incident counters do not balance")
    if rehearsal["inconclusive_incidents"] > rehearsal["incident_count"]:
        report.error("$.latest_final.readbacks.get_rehearsal.result.inconclusive_incidents", "exceeds incident_count")

    status, anchor, impact = incident["decision_status"], incident["anchor_index"], incident["impact"]
    expected_report_args = [incident["client_reference"], incident["report"]]
    if expected_report_args not in [tx["args"] for tx in by_purpose.get("report_incident", [])]:
        report.error("$.transactions", "no report_incident args match the returned incident")
    if [incident["record_id"]] not in [tx["args"] for tx in by_purpose.get("triage_incident", [])]:
        report.error("$.transactions", "no triage_incident args identify the returned incident")
    if status:
        decision = {"status": status, "anchor_index": anchor, "impact": impact}
        if not _decision_ok(decision):
            report.error("$.latest_final.readbacks.get_incident.result", "stored semantic decision is incoherent")
        matches = [tx["equality_output"] for tx in txs if tx["purpose"] == "triage_incident"]
        if decision not in matches:
            report.error("$.latest_final.readbacks.get_incident.result", "no triage equality output matches final state")
        if status == "MATCHED":
            if anchor >= len(cues) or incident["anchor_cue"] != cues[anchor]["cue_id"]:
                report.error("$.latest_final.readbacks.get_incident.result.anchor_cue", "does not match anchor_index")
            if impact == "CHAIN" and anchor == len(cues) - 1:
                report.error("$.latest_final.readbacks.get_incident.result.impact", "CHAIN requires a successor")
            summary = f"MATCHED|{incident['anchor_cue']}|{impact}"
        else:
            if incident["anchor_cue"]:
                report.error("$.latest_final.readbacks.get_incident.result.anchor_cue", "must be empty")
            summary = status
        if incident["summary"] != summary:
            report.error("$.latest_final.readbacks.get_incident.result.summary", "not contract-derived")
        allowed = _allowed(status, impact)
        if incident["allowed_actions_json"] != canonical(allowed).decode():
            report.error("$.latest_final.readbacks.get_incident.result.allowed_actions_json", "not contract-derived")
        if incident["action"] and incident["action"] not in allowed:
            report.error("$.latest_final.readbacks.get_incident.result.action", "is not an allowed action")
        if incident["action"] and [incident["record_id"], incident["action"]] not in [
            tx["args"] for tx in by_purpose.get("choose_recovery", [])
        ]:
            report.error("$.transactions", "no choose_recovery args match the stored action")

    workflow = incident["workflow"]
    if workflow == "REPORTED" and incident["record_digest"] != incident["creation_digest"]:
        report.error("$.latest_final.readbacks.get_incident.result.record_digest", "reported record must equal creation digest")
    if workflow in ("ACKNOWLEDGED", "CANCELLED") and incident["record_digest"] != incident["final_digest"]:
        report.error("$.latest_final.readbacks.get_incident.result.record_digest", "closed record must equal final digest")
    if workflow == "ACKNOWLEDGED" and (not incident["acknowledged"] or incident["cancel_reason"]):
        report.error("$.latest_final.readbacks.get_incident.result", "acknowledged workflow fields are incoherent")
    if workflow == "ACKNOWLEDGED" and [incident["record_id"]] not in [
        tx["args"] for tx in by_purpose.get("acknowledge_recovery", [])
    ]:
        report.error("$.transactions", "no acknowledgement args match the closed incident")
    if workflow == "CANCELLED" and (incident["acknowledged"] or not incident["cancel_reason"]):
        report.error("$.latest_final.readbacks.get_incident.result", "cancelled workflow fields are incoherent")
    if workflow == "CANCELLED" and [incident["record_id"], incident["cancel_reason"]] not in [
        tx["args"] for tx in by_purpose.get("cancel_incident", [])
    ]:
        report.error("$.transactions", "no cancellation args match the closed incident")
    if workflow == "ACTION_SELECTED" and not incident["action"]:
        report.error("$.latest_final.readbacks.get_incident.result.action", "action-selected workflow requires an action")


def _evidence_checks(data: dict[str, Any], root: Path, files: bool, report: Report) -> None:
    if not all(_network_url(data["network"][key]) for key in ("rpc_url", "explorer_url")):
        report.error("$.network", "endpoints require HTTPS, except loopback localnet URLs")
    if not _https(data["repository"]["url"]):
        report.error("$.repository.url", "must be an absolute HTTPS URL")
    if not urlparse(data["$schema"]).path.endswith("schema.json"):
        report.error("$.$schema", "must identify deployments/schema.json")
    capture = data["raw_capture"]
    public_hash = sha256(canonical(capture["record"]))
    if capture["public_sha256"] != public_hash:
        report.error("$.raw_capture.public_sha256", f"digest mismatch; actual {public_hash}")
    if not capture["additional_redactions"] and capture["original_sha256"] != public_hash:
        report.error("$.raw_capture.original_sha256", "must equal public_sha256 when nothing was redacted")
    redacted_again, leaked = _redact(capture["record"])
    if redacted_again != capture["record"] or leaked:
        report.error("$.raw_capture.record", "contains credential-bearing fields that are not redacted")
    raw_transactions = capture["record"].get("transactions", {})
    if isinstance(raw_transactions, dict):
        for label, proof in raw_transactions.items():
            if not isinstance(proof, dict) or not isinstance(proof.get("receipt"), dict):
                continue
            expected = proof.get("redacted_receipt_sha256")
            receipt_hash = sha256(canonical(proof["receipt"]))
            if expected != receipt_hash:
                report.error(
                    f"$.raw_capture.record.transactions.{label}.redacted_receipt_sha256",
                    f"digest mismatch; actual {receipt_hash}",
                )
    if files:
        _repository_checks(data, root, report)
    else:
        report.warn("$.repository", "local file and Git provenance checks were skipped")
    txs = _transaction_checks(data, report)
    _final_checks(data, txs, report)
    tx_hashes = {tx["tx_hash"].lower() for tx in txs}
    for group in ("limitations", "warnings"):
        for index, note in enumerate(data[group]):
            related = note.get("related_tx")
            if related and related.lower() not in tx_hashes:
                report.error(f"$.{group}[{index}].related_tx", "is not in transactions")
    observed = datetime.fromisoformat(data["latest_final"]["observed_at"].replace("Z", "+00:00"))
    generated = datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
    last_tx = datetime.fromisoformat(txs[-1]["created_at"].replace("Z", "+00:00"))
    if observed < last_tx or generated < observed:
        report.error("$.generated_at", "must follow the latest transaction and final readback")


def validate(
    data: Any,
    schema: dict[str, Any],
    source: str,
    root: Path,
    files: bool,
    require_evidence: bool,
) -> Report:
    report = Report(source)
    if not isinstance(data, dict):
        report.error("$", "manifest must be an object")
        return report
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        report.error("schema", "missing $defs")
        return report
    if data.get("schema") == EVIDENCE_SCHEMA:
        report.grade = "evidence-v2"
        shape = defs.get("evidenceManifest")
    elif {"deploy_tx", "intelligent_tx", "observed"}.issubset(data):
        report.grade = "legacy-v1"
        shape = defs.get("legacyManifest")
    else:
        report.error("$.schema", f"must equal {EVIDENCE_SCHEMA!r}")
        return report
    if not isinstance(shape, dict):
        report.error("schema", f"missing definition for {report.grade}")
        return report
    report.errors.extend(schema_errors(data, shape, schema))
    if report.errors:
        return report
    if report.grade == "legacy-v1":
        report.warn("$", "legacy evidence lacks commit, full transaction, vote, and readback binding")
        if require_evidence:
            report.error("$", "--require-evidence-grade rejects legacy evidence")
        return report
    _evidence_checks(data, root, files, report)
    return report


RAW_STEPS = (
    ("deployment", "deploy"),
    ("authorize_reporter", "set_reporter"),
    ("register_cue_open", "register_cue"),
    ("register_cue_cross", "register_cue"),
    ("register_cue_close", "register_cue"),
    ("seal_cue_sheet", "seal_cue_sheet"),
    ("report_incident", "report_incident"),
    ("triage_incident", "triage_incident"),
    ("choose_recovery", "choose_recovery"),
    ("acknowledge_recovery", "acknowledge_recovery"),
)


def _tx_hash(proof: dict[str, Any], receipt: dict[str, Any]) -> tuple[str, str]:
    candidates = []
    if isinstance(receipt.get("hash"), str):
        candidates.append((receipt["hash"], "receipt.hash"))
    for entry in proof.get("transaction_identifiers", []):
        if isinstance(entry, dict):
            candidates.append((entry.get("value"), f"transaction_identifiers:{entry.get('path', '')}"))
    for value, path in candidates:
        if isinstance(value, str) and re.fullmatch(r"0x[0-9A-Fa-f]{64}", value):
            return value.lower(), path
    raise JsonError("receipt has no 32-byte transaction identifier")


def _leader(receipt: dict[str, Any]) -> tuple[dict[str, Any], str]:
    consensus = receipt.get("consensus_data")
    if not isinstance(consensus, dict):
        raise JsonError("receipt lacks consensus_data")
    values = consensus.get("leader_receipt")
    if not isinstance(values, list) or not values:
        raise JsonError("receipt lacks leader_receipt")
    index = next(
        (position for position, value in enumerate(values) if isinstance(value, dict) and value.get("mode") == "leader"),
        0,
    )
    if not isinstance(values[index], dict):
        raise JsonError("leader receipt is not an object")
    return values[index], f"receipt.consensus_data.leader_receipt[{index}]"


def _find_state_hash(value: Any, path: str = "receipt") -> tuple[str | None, str]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.lower() in {"state_hash", "state_root", "statehash", "stateroot"} and isinstance(child, str):
                if re.fullmatch(r"(0x)?[0-9a-f]{64}", child):
                    return child, child_path
            found, found_path = _find_state_hash(child, child_path)
            if found is not None:
                return found, found_path
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found, found_path = _find_state_hash(child, f"{path}[{index}]")
            if found is not None:
                return found, found_path
    return None, "NOT_EXPOSED_BY_RECEIPT"


def _find_decision(value: Any, path: str) -> tuple[dict[str, Any] | None, str]:
    if isinstance(value, dict):
        if set(value) == {"status", "anchor_index", "impact"} and _decision_ok(value):
            return value, path
        readable = value.get("readable")
        if isinstance(readable, str):
            try:
                decoded = json.loads(readable, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
            except (ValueError, TypeError):
                decoded = None
            found, found_path = _find_decision(decoded, f"{path}.readable")
            if found is not None:
                return found, found_path
        for key, child in value.items():
            found, found_path = _find_decision(child, f"{path}.{key}")
            if found is not None:
                return found, found_path
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found, found_path = _find_decision(child, f"{path}[{index}]")
            if found is not None:
                return found, found_path
    return None, path


def _normalize_tx(
    raw: dict[str, Any], label: str, purpose: str, order: int
) -> dict[str, Any]:
    proof = raw["transactions"].get(label)
    if not isinstance(proof, dict) or not isinstance(proof.get("receipt"), dict):
        raise JsonError(f"transactions.{label} is not a stored receipt proof")
    receipt = proof["receipt"]
    intent = proof.get("submission_intent")
    if not isinstance(intent, dict) or intent.get("label") != label or not isinstance(intent.get("args"), list):
        raise JsonError(f"transactions.{label} lacks its submission intent")
    args = intent["args"]
    if intent.get("args_sha256") != sha256(canonical(args)):
        raise JsonError(f"transactions.{label} submission args commitment differs")
    tx_hash, hash_path = _tx_hash(proof, receipt)
    leader, leader_path = _leader(receipt)
    votes_raw = receipt.get("consensus_data", {}).get("votes")
    if not isinstance(votes_raw, dict) or not votes_raw:
        raise JsonError(f"transactions.{label} lacks classified votes")
    counts = {"agree": 0, "disagree": 0, "idle": 0, "other": 0}
    for vote in votes_raw.values():
        name = str(vote).lower().split(".")[-1]
        counts[name if name in counts else "other"] += 1
    status = str(receipt.get("status_name", "")).upper().split(".")[-1]
    result = str(receipt.get("result_name", "")).upper().split(".")[-1]
    if status != "FINALIZED" or result not in {"AGREE", "MAJORITY_AGREE"}:
        raise JsonError(f"transactions.{label} is not finalized with agreement")
    try:
        rounds = int(receipt["num_of_rounds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise JsonError(f"transactions.{label} lacks a round count") from exc
    created_at = receipt.get("created_at", proof.get("finalized_recorded_at"))
    sender = intent.get("sender")
    receipt_sender = receipt.get("from_address", receipt.get("sender"))
    destination = receipt.get("to_address", receipt.get("recipient", raw["contract_address"]))
    if not all(isinstance(value, str) for value in (created_at, sender, destination)):
        raise JsonError(f"transactions.{label} lacks timestamp/from/to evidence")
    assert isinstance(sender, str)
    if isinstance(receipt_sender, str) and receipt_sender.lower() != sender.lower():
        raise JsonError(f"transactions.{label} receipt sender differs from submission intent")
    execution_result = str(leader.get("execution_result", "")).upper().split(".")[-1]
    result_record = leader.get("result")
    return_status = result_record.get("status") if isinstance(result_record, dict) else None
    genvm = leader.get("genvm_result")
    stderr = genvm.get("stderr", "") if isinstance(genvm, dict) else ""
    if execution_result != "SUCCESS" or return_status != "return" or not isinstance(stderr, str):
        raise JsonError(f"transactions.{label} lacks successful leader execution evidence")
    state_hash, state_path = _find_state_hash(receipt)
    equality: dict[str, Any] | None = None
    equality_path = "DETERMINISTIC_WRITE_NO_EQUALITY_OUTPUT"
    if purpose == "triage_incident":
        equality, equality_path = _find_decision(leader.get("eq_outputs"), f"{leader_path}.eq_outputs")
        if equality is None:
            raise JsonError("triage receipt has no coherent semantic equality output")
    warnings = []
    if stderr:
        warnings.append({"code": "LEADER_STDERR", "message": stderr[:2000], "related_tx": tx_hash})
    return {
        "order": order,
        "purpose": purpose,
        "tx_hash": tx_hash,
        "method": METHODS[purpose],
        "args": args,
        "args_sha256": sha256(canonical(args)),
        "created_at": created_at,
        "from": sender,
        "to": destination,
        "status": status,
        "result": result,
        "rounds": rounds,
        "votes": {"total": len(votes_raw), **counts, "evidence_path": "receipt.consensus_data.votes"},
        "execution": {
            "leader": execution_result,
            "return_status": return_status,
            "state_hash": state_hash,
            "stderr": stderr,
        },
        "equality_output": equality,
        "evidence_paths": {
            "tx_hash": hash_path,
            "args": f"transactions.{label}.submission_intent.args",
            "created_at": "receipt.created_at",
            "from": f"transactions.{label}.submission_intent.sender",
            "to": "receipt.to_address|receipt.recipient|contract_address",
            "status": "receipt.status_name",
            "result": "receipt.result_name",
            "rounds": "receipt.num_of_rounds",
            "votes": "receipt.consensus_data.votes",
            "execution": leader_path,
            "state_hash": state_path,
            "equality_output": equality_path,
            "unredacted_receipt_sha256": f"transactions.{label}.unredacted_receipt_sha256",
            "redacted_receipt_sha256": f"transactions.{label}.redacted_receipt_sha256",
        },
        "explorer_url": raw["network"]["explorer"].rstrip("/") + "/transactions/" + tx_hash,
        "warnings": warnings,
    }


def _tool_versions(raw: dict[str, Any]) -> dict[str, str]:
    versions = raw.get("versions", {})

    def command(*args: str) -> str:
        try:
            completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5, check=False)
            return completed.stdout.strip().splitlines()[0][:200] if completed.returncode == 0 and completed.stdout.strip() else "NOT_CAPTURED"
        except (OSError, subprocess.SubprocessError):
            return "NOT_CAPTURED"

    return {
        "python": sys.version.split()[0],
        "genlayer_cli": command("genlayer", "--version"),
        "genlayer_js": "NOT_USED_BY_PYTHON_HARNESS",
        "genlayer_test": str(versions.get("genlayer_test", "NOT_CAPTURED")),
        "genvm_linter": command("genvm-lint", "--version"),
        "pyright": command("pyright", "--version"),
        "pytest": command("pytest", "--version"),
    }


def normalize(raw: Any, root: Path) -> dict[str, Any]:
    """Convert one COMPLETE deploy harness checkpoint to public evidence V2."""
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise JsonError("raw checkpoint must use schema_version 2")
    if raw.get("record_status") != "COMPLETE" or raw.get("pending_operation") is not None:
        raise JsonError("raw checkpoint is not COMPLETE and settled")
    transactions = raw.get("transactions")
    if not isinstance(transactions, dict) or set(transactions) != {label for label, _ in RAW_STEPS}:
        raise JsonError("raw checkpoint does not contain the exact smoke lifecycle")
    views = raw.get("latest_final_readbacks")
    fixture = raw.get("fixture")
    if not isinstance(views, dict) or not isinstance(fixture, dict):
        raise JsonError("raw checkpoint lacks final readbacks or fixture")
    incident_id = fixture.get("record_id")
    try:
        incident = views["incidents_by_id"][incident_id]
        rehearsal = views["rehearsal"]
        policy = views["policy"]
        cues = views["cues_by_index"]
        reporters = views["reporters_by_index"]
    except (KeyError, TypeError) as exc:
        raise JsonError("raw checkpoint final readbacks are incomplete") from exc
    readbacks = {
        "get_incident": {"args": [incident_id], "result": incident},
        "get_rehearsal": {"args": [], "result": rehearsal},
        "get_policy": {"args": [], "result": policy},
        "cues": [{"method": "get_cue_at", "args": [index], "result": value} for index, value in enumerate(cues)],
        "reporters": [
            {"method": "get_reporter_at", "args": [index], "result": value}
            for index, value in enumerate(reporters)
        ],
    }
    source, abi = raw["source"], raw["abi"]
    abi_path = _safe_file(root, abi["path"])
    if abi_path is None or not abi_path.is_file():
        raise JsonError("raw ABI path is not available under repo root")
    constructor = {"args": raw["constructor"]["args"], "kwargs": {}}
    constructor.update(
        {"canonicalization": "json-sort-keys-compact-utf8", "fixture_sha256": sha256(canonical(constructor))}
    )
    public_raw, extra_redactions = _redact(raw)
    limitations = [
        {"code": f"HARNESS_LIMITATION_{index + 1:02d}", "message": str(message)[:2000]}
        for index, message in enumerate(raw.get("limitations", []))
    ]
    if not limitations:
        limitations = [{"code": "LIMITATIONS_NOT_CAPTURED", "message": "The raw harness recorded no limitations."}]
    warnings = [{
        "code": "RAW_RECEIPTS_REDACTED",
        "message": "Raw receipts retain unredacted commitments but credential-bearing values are excluded from this public manifest.",
    }]
    if extra_redactions:
        warnings.append({"code": "ADDITIONAL_EXPORT_REDACTIONS", "message": f"Exporter redacted {len(extra_redactions)} additional credential field(s)."})
    generated_at = raw.get("updated_at")
    if not isinstance(generated_at, str):
        raise JsonError("raw checkpoint lacks updated_at")
    return {
        "$schema": "./schema.json",
        "schema": EVIDENCE_SCHEMA,
        "generated_at": generated_at,
        "network": {
            "name": raw["network"]["name"],
            "chain_id": raw["network"]["chain_id"],
            "rpc_url": raw["network"]["rpc"],
            "explorer_url": raw["network"]["explorer"],
        },
        "repository": {
            "url": raw["repository"],
            "commit": raw["source_commit"],
            "contract_path": source["path"],
            "abi_path": abi["path"],
            "contract_git_blob_sha1": source["git_blob_id"],
            "contract_source_sha256": source["sha256"],
            "source_canonicalization": "utf8-lf",
            "abi_schema_sha256": sha256(canonical_document(abi_path.read_bytes())),
            "abi_canonicalization": "json-sort-keys-compact-utf8",
        },
        "deployment": {
            "deployer": raw["manager"],
            "contract_address": raw["contract_address"],
            "constructor": constructor,
        },
        "transactions": [
            _normalize_tx(raw, label, purpose, index)
            for index, (label, purpose) in enumerate(RAW_STEPS)
        ],
        "latest_final": {
            "observed_at": generated_at,
            "transaction_hash_variant": "LATEST_FINAL",
            "readbacks": readbacks,
            "readbacks_sha256": sha256(canonical(readbacks)),
        },
        "tools": _tool_versions(raw),
        "limitations": limitations,
        "warnings": warnings,
        "raw_capture": {
            "canonicalization": "json-sort-keys-compact-utf8",
            "original_sha256": sha256(canonical(raw)),
            "public_sha256": sha256(canonical(public_raw)),
            "additional_redactions": extra_redactions,
            "record": public_raw,
        },
    }


def self_test(schema: dict[str, Any], root: Path) -> list[str]:
    """Exercise legacy compatibility plus core parser/redaction safeguards."""
    failures: list[str] = []
    try:
        legacy = load_json(root / "deployments" / "studionet.json")
    except (OSError, ValueError) as exc:
        return [f"cannot load legacy fixture: {exc}"]
    if not validate(legacy, schema, "legacy", root, False, False).valid:
        failures.append("legacy manifest should validate")
    if validate(legacy, schema, "legacy-strict", root, False, True).valid:
        failures.append("strict mode should reject legacy evidence")
    malformed = {**legacy, "deploy_tx": "0x1234"}
    if validate(malformed, schema, "malformed", root, False, False).valid:
        failures.append("schema should reject a malformed transaction hash")
    scrubbed, paths = _redact({"nested": {"private_key": "secret"}})
    if scrubbed != {"nested": {"private_key": "[REDACTED]"}} or not paths:
        failures.append("credential redaction failed")
    try:
        json.loads('{"a":1,"a":2}', object_pairs_hook=_unique_object)
        failures.append("duplicate JSON keys were accepted")
    except JsonError:
        pass
    try:
        json.loads("NaN", parse_constant=_nonfinite)
        failures.append("non-finite JSON number was accepted")
    except JsonError:
        pass
    if canonical({"b": 1, "a": 2}) != b'{"a":2,"b":1}':
        failures.append("canonical JSON is unstable")
    if _network_url("http://example.com") or not _network_url("http://127.0.0.1:4000"):
        failures.append("network URL policy is incorrect")
    return failures


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("manifest", nargs="?", default="deployments/studionet.json")
    result.add_argument("--schema", default="deployments/schema.json")
    result.add_argument("--repo-root", default=".")
    result.add_argument("--skip-files", action="store_true", help="skip local and Git provenance checks")
    result.add_argument("--require-evidence-grade", action="store_true")
    result.add_argument("--self-test", action="store_true")
    result.add_argument("--json-output", action="store_true")
    result.add_argument("--normalize", metavar="RAW", help="normalize a COMPLETE V2 harness checkpoint")
    result.add_argument("--output", metavar="FILE", help="new immutable manifest path for --normalize")
    return result


def _print_report(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.json(), indent=2))
        return
    print(f"{'PASS' if report.valid else 'FAIL'} [{report.grade}] {report.source}")
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root, schema_path = Path(args.repo_root).resolve(), Path(args.schema).resolve()
    try:
        schema = load_json(schema_path)
        if not isinstance(schema, dict):
            raise JsonError("schema root is not an object")
        version = schema["$defs"]["evidenceManifest"]["properties"]["schema"]["const"]
        if version != EVIDENCE_SCHEMA:
            raise JsonError(f"schema version {version!r} is unsupported")
        if args.self_test:
            failures = self_test(schema, root)
            if failures:
                print("SELF-TEST FAIL")
                print("\n".join(failures))
                return 1
            print("SELF-TEST PASS: 8 cases")
            return 0
        if args.normalize:
            if not args.output:
                raise JsonError("--normalize requires --output")
            raw_path, output_path = Path(args.normalize).resolve(), Path(args.output).resolve()
            if output_path.exists():
                raise JsonError(f"refusing to overwrite immutable output {output_path}")
            data = normalize(load_json(raw_path), root)
            report = validate(
                data, schema, f"normalized from {raw_path}", root, not args.skip_files, True
            )
            _print_report(report, args.json_output)
            if not report.valid:
                return 1
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_suffix(output_path.suffix + ".tmp")
            if temporary.exists():
                raise JsonError(f"refusing to overwrite temporary file {temporary}")
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
            temporary.replace(output_path)
            print(f"WROTE: {output_path}")
            return 0
        if args.output:
            raise JsonError("--output is only valid with --normalize")
        manifest_path = Path(args.manifest).resolve()
        data = load_json(manifest_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    report = validate(
        data, schema, str(manifest_path), root, not args.skip_files, args.require_evidence_grade
    )
    _print_report(report, args.json_output)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
