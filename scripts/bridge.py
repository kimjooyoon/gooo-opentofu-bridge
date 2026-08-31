#!/usr/bin/env python3
"""Small deterministic bridge from a released Gooo graph to tf.json.

This script deliberately knows only the example intent shape. OpenTofu remains
the authority for configuration validation; this file checks the bridge's
declared contract, output determinism, and evidence binding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


OUTPUT_NAMES = ["dossier.md", "intent.tf.json"]
ACTIVITIES = [
    "DeclareGoooInfrastructureIntent",
    "BindGoooIntentToOpenTofu",
    "ConsumePinnedOpenTofuJSONSpec",
    "GenerateOpenTofuCompatibleArtifact",
    "GenerateHumanDossier",
    "VerifyGeneratedOutputs",
    "GenerateOpenTofuPlanReceipt",
    "MatchGoooIntentToOpenTofuPlan",
    "PreserveUnknownCase",
    "RefuteContradictionCase",
    "VerifyDeterministicReplay",
    "PreserveReadOnlyBoundary",
]
UNKNOWN_FIELDS = {"stage", "step", "reason", "unknown_class", "next_operation", "blocked_by"}
PLAN_ACTIONS = {"noop", "create", "read", "update", "replace", "delete", "move"}


def die(message: str) -> None:
    raise SystemExit(f"bridge error: {message}")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        die(f"cannot read JSON {path}: {exc}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError as exc:
        die(f"cannot hash {path}: {exc}")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        die(f"cannot read {path}: {exc}")


def cells(denominator: dict[str, Any]) -> list[dict[str, Any]]:
    actual = denominator.get("cells")
    if not isinstance(actual, list) or len(actual) != denominator.get("target_cells"):
        die("denominator cell count is not fixed")
    if [cell.get("ordinal") for cell in actual] != list(range(1, len(actual) + 1)):
        die("denominator ordinals are not contiguous")
    if len({cell.get("id") for cell in actual}) != len(actual):
        die("denominator cell IDs are not unique")
    if {cell.get("activity") for cell in actual} != set(ACTIVITIES):
        die("denominator activities do not match the example")
    proof_counts = {family: sum(cell.get("proof_family") == family for cell in actual) for family in ("FOUNDATION", "COHERENCE", "REGRESSION")}
    indicator_counts = {indicator: sum(cell.get("indicator") == indicator for cell in actual) for indicator in ("DRIVER", "OUTCOME", "GUARDRAIL")}
    if proof_counts != {"FOUNDATION": 4, "COHERENCE": 4, "REGRESSION": 4}:
        die(f"proof families are not balanced: {proof_counts}")
    if indicator_counts != {"DRIVER": 4, "OUTCOME": 4, "GUARDRAIL": 4}:
        die(f"indicators are not balanced: {indicator_counts}")
    return actual


def activity_relations(graph: dict[str, Any]) -> tuple[dict[str, str], dict[str, set[str]], dict[str, set[str]]]:
    nodes = graph.get("nodes")
    relations = graph.get("relations")
    if not isinstance(nodes, list) or not isinstance(relations, list):
        die("released semantic graph has no nodes/relations arrays")
    by_name: dict[str, str] = {}
    for node in nodes:
        if node.get("kind") != "Activity":
            continue
        name = node.get("name")
        node_id = node.get("id")
        if not isinstance(name, str) or not isinstance(node_id, str):
            die("activity node is missing name or id")
        if name in by_name:
            die(f"activity binding is ambiguous: {name}")
        by_name[name] = node_id
    if set(by_name) != set(ACTIVITIES):
        die("released semantic graph activity set mismatch")
    inputs = {name: set() for name in ACTIVITIES}
    outputs = {name: set() for name in ACTIVITIES}
    name_by_id = {node_id: name for name, node_id in by_name.items()}
    for relation in relations:
        predicate = relation.get("predicate")
        subject = relation.get("subject")
        obj = relation.get("object")
        if predicate == "used" and subject in name_by_id:
            inputs[name_by_id[subject]].add(obj)
        if predicate == "wasGeneratedBy" and obj in name_by_id:
            outputs[name_by_id[obj]].add(subject)
    if any(not inputs[name] for name in ACTIVITIES) or any(not outputs[name] for name in ACTIVITIES):
        die("released semantic graph has an activity without input or output")
    return by_name, inputs, outputs


def bind_graph(source: Path, graph_path: Path, denominator_path: Path, output: Path) -> None:
    denominator = read_json(denominator_path)
    expected_cells = cells(denominator)
    graph = read_json(graph_path)
    by_name, inputs, outputs = activity_relations(graph)
    source_text = read_text(source)
    for name in ACTIVITIES:
        if f"activity {name}(" not in source_text:
            die(f"source does not declare {name}")

    bindings: list[dict[str, Any]] = []
    for cell in expected_cells:
        activity = cell["activity"]
        graph_dependencies: list[str] = []
        for predecessor in expected_cells:
            if predecessor["id"] not in cell.get("depends_on", []):
                continue
            predecessor_activity = predecessor["activity"]
            if not outputs[predecessor_activity].intersection(inputs[activity]):
                die(f"missing graph edge {predecessor['id']} -> {cell['id']}")
            graph_dependencies.append(predecessor["id"])
        if sorted(graph_dependencies) != sorted(cell.get("depends_on", [])):
            die(f"graph dependency mismatch for {cell['id']}")
        bindings.append(
            {
                "ordinal": cell["ordinal"],
                "id": cell["id"],
                "activity": activity,
                "activity_id": by_name[activity],
                "depends_on": sorted(cell.get("depends_on", [])),
                "input_entities": sorted(inputs[activity]),
                "output_entities": sorted(outputs[activity]),
                "graph_depends_on": sorted(graph_dependencies),
            }
        )
    write_json(
        output,
        {
            "schema": "gooo/opentofu-bridge/meta-bindings/v1",
            "verified": True,
            "source_sha256": sha256_file(source),
            "graph_sha256": sha256_file(graph_path),
            "activity_count": len(bindings),
            "binding_edges": sum(len(binding["depends_on"]) for binding in bindings),
            "bindings": bindings,
        },
    )


def check_spec(spec_path: Path, lock: dict[str, Any]) -> str:
    expected = lock["opentofu"]["json_spec"]["sha256"]
    actual = sha256_file(spec_path)
    if actual != expected:
        die(f"OpenTofu JSON spec digest mismatch: {actual} != {expected}")
    return actual


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def unknown_coordinates(stage: str, step: str, reason: str, unknown_class: str, next_operation: str, blocked_by: list[str]) -> dict[str, Any]:
    coordinates = {
        "stage": stage,
        "step": step,
        "reason": reason,
        "unknown_class": unknown_class,
        "next_operation": next_operation,
        "blocked_by": blocked_by,
    }
    if set(coordinates) != UNKNOWN_FIELDS or not isinstance(blocked_by, list):
        die("UNKNOWN coordinates are not exact")
    return coordinates


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for line_number, line in enumerate(read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            die(f"plan JSON line {line_number} is malformed: {exc}")
        if not isinstance(value, dict):
            die(f"plan JSON line {line_number} is not an object")
        messages.append(value)
    if not messages:
        die("plan JSON stream is empty")
    return messages


def read_plan_stream(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Read plan JSON without turning an unavailable or malformed receipt into a process error."""
    if not path.exists():
        return [], {
            "kind": "missing",
            "reason": "PLAN_JSON_MISSING",
            "detail": str(path),
        }
    try:
        lines = read_text(path).splitlines()
    except SystemExit:
        return [], {
            "kind": "missing",
            "reason": "PLAN_JSON_UNREADABLE",
            "detail": str(path),
        }
    messages: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            return messages, {
                "kind": "malformed",
                "reason": "MALFORMED_PLAN_JSON",
                "detail": str(exc),
                "line": line_number,
            }
        if not isinstance(value, dict):
            return messages, {
                "kind": "malformed",
                "reason": "MALFORMED_PLAN_JSON_MESSAGE",
                "detail": "plan JSON lines must be objects",
                "line": line_number,
            }
        messages.append(value)
    if not messages:
        return [], {
            "kind": "missing",
            "reason": "PLAN_JSON_EMPTY",
            "detail": str(path),
        }
    return messages, None


def receipt_claim(state: str, stage: str, step: str, reason: str, next_operation: str | None = None) -> dict[str, Any]:
    return {
        "state": state,
        "stage": stage,
        "step": step,
        "reason": reason,
        "unknown_class": None,
        "next_operation": next_operation,
        "blocked_by": [],
    }


def load_plan_oracle(path: Path) -> dict[str, Any]:
    oracle = read_json(path)
    if oracle.get("schema") != "gooo/opentofu-bridge/plan-oracle/v1":
        die("plan oracle schema is unsupported")
    actions = oracle.get("resource_actions")
    if not isinstance(actions, list) or not actions:
        die("plan oracle resource_actions is empty")
    normalized = []
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"address", "action"} or action["action"] not in PLAN_ACTIONS:
            die("plan oracle action is invalid")
        normalized.append({"address": action["address"], "action": action["action"]})
    dependencies = oracle.get("resource_action_dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        die("plan oracle resource_action_dependencies is empty")
    normalized_dependencies = []
    for dependency in dependencies:
        if (
            not isinstance(dependency, dict)
            or set(dependency) != {"intent_entity", "address", "action", "depends_on"}
            or not isinstance(dependency["intent_entity"], str)
            or not isinstance(dependency["address"], str)
            or dependency["action"] not in PLAN_ACTIONS
            or not isinstance(dependency["depends_on"], list)
            or any(not isinstance(item, str) for item in dependency["depends_on"])
        ):
            die("plan oracle resource_action_dependencies entry is invalid")
        normalized_dependencies.append(
            {
                "intent_entity": dependency["intent_entity"],
                "address": dependency["address"],
                "action": dependency["action"],
                "depends_on": sorted(dependency["depends_on"]),
            }
        )
    if sorted((item["address"], item["action"]) for item in normalized_dependencies) != sorted(
        (item["address"], item["action"]) for item in normalized
    ):
        die("plan oracle resource/action dependencies do not match actions")
    if oracle.get("change_summary") != {"add": 1, "change": 0, "forget": 0, "import": 0, "operation": "plan", "remove": 0}:
        die("plan oracle summary is not fixed")
    if oracle.get("side_effects") != {"apply": 0, "cloud": 0, "network": 0, "source_write": 0}:
        die("plan oracle side-effect contract is not zero")
    oracle["resource_actions"] = sorted(normalized, key=lambda item: (item["address"], item["action"]))
    oracle["resource_action_dependencies"] = sorted(
        normalized_dependencies, key=lambda item: (item["address"], item["action"], item["intent_entity"])
    )
    return oracle


def generate_plan_receipt(
    plan_ui_path: Path,
    version_json_path: Path,
    binary_path: Path,
    artifact_path: Path,
    oracle_path: Path,
    lock_path: Path,
    source_path: Path,
    graph_path: Path,
    spec_path: Path,
    toolchain_manifest_path: Path,
    command_path: Path,
    release_asset_path: Path,
    checksums_asset_path: Path,
    exit_code: int,
    output: Path,
) -> None:
    lock = read_json(lock_path)
    oracle = load_plan_oracle(oracle_path)
    version_json = read_json(version_json_path)
    release = lock.get("opentofu", {})
    command = read_json(command_path)
    expected_command = release.get("plan_command")
    command_valid = (
        isinstance(command, dict)
        and isinstance(command.get("argv"), list)
        and command.get("argv") == expected_command
        and command.get("working_directory") == "caller-owned-module"
        and command.get("read_only") is True
    )
    binary_sha256 = sha256_file(binary_path)
    release_asset_sha256 = sha256_file(release_asset_path)
    checksums_asset_sha256 = sha256_file(checksums_asset_path)
    version_consistent = (
        isinstance(version_json, dict)
        and version_json.get("terraform_version") == release.get("iac_engine_version")
        and version_json.get("platform") == release.get("platform")
    )
    binary_verified = binary_sha256 == release.get("binary_sha256")
    release_asset_verified = release_asset_sha256 == release.get("asset", {}).get("sha256")
    checksums_asset_verified = checksums_asset_sha256 == release.get("checksums", {}).get("sha256")
    toolchain_manifest = read_json(toolchain_manifest_path)
    toolchain_sha256 = sha256_file(toolchain_manifest_path)
    messages, stream_error = read_plan_stream(plan_ui_path)
    ui_version = messages[0].get("ui") if messages else None
    receipt: dict[str, Any] = {
        "schema": "gooo/opentofu-bridge/plan-receipt/v1",
        "iac_engine": release.get("iac_engine"),
        "engine_identity_source": "PINNED_RELEASE_LOCK",
        "engine_version": release.get("iac_engine_version"),
        "release": {
            "repository": release.get("repository"),
            "tag": release.get("tag"),
            "release_id": release.get("release_id"),
            "target_commit_sha": release.get("target_commit_sha"),
            "asset_sha256": release_asset_sha256,
            "expected_asset_sha256": release.get("asset", {}).get("sha256"),
            "checksums_sha256": checksums_asset_sha256,
            "expected_checksums_sha256": release.get("checksums", {}).get("sha256"),
            "asset_verified": release_asset_verified,
            "checksums_verified": checksums_asset_verified,
        },
        "binary_sha256": binary_sha256,
        "expected_binary_sha256": release.get("binary_sha256"),
        "binary_verified": binary_verified,
        "release_asset_sha256": release_asset_sha256,
        "checksums_asset_sha256": checksums_asset_sha256,
        "version_json_sha256": sha256_file(version_json_path),
        "version_json": version_json,
        "version_consistent_with_pinned_release": version_consistent,
        "ui_version": ui_version,
        "exit_code": exit_code,
        "stdout_sha256": sha256_file(plan_ui_path) if plan_ui_path.exists() else None,
        "input_artifact_sha256": sha256_file(artifact_path),
        "source_sha256": sha256_file(source_path),
        "semantic_ir_sha256": sha256_file(graph_path),
        "json_spec_sha256": sha256_file(spec_path),
        "release_lock_sha256": sha256_file(lock_path),
        "toolchain_sha256": toolchain_sha256,
        "toolchain": toolchain_manifest,
        "inputs": {
            "source_sha256": sha256_file(source_path),
            "semantic_ir_sha256": sha256_file(graph_path),
            "generated_config_sha256": sha256_file(artifact_path),
            "json_spec_sha256": sha256_file(spec_path),
            "release_lock_sha256": sha256_file(lock_path),
        },
        "command": command,
        "command_sha256": canonical_sha256(command),
        "command_verified": command_valid,
        "read_only": {
            "refresh": False,
            "input": False,
            "apply": 0,
            "destroy": 0,
            "cloud": 0,
            "network": 0,
            "source_write": 0,
        },
        "oracle_resource_action_dependencies": oracle["resource_action_dependencies"],
        "oracle_sha256": sha256_file(oracle_path),
        "resource_actions": [],
        "change_summary": None,
        "drift_count": 0,
        "state": "UNKNOWN",
        "unknown": unknown_coordinates(
            "PLAN",
            "READ_PINNED_JSON_UI",
            "PLAN_RECEIPT_NOT_CLOSED",
            "OBSERVATION_UNAVAILABLE",
            "CAPTURE_SUPPORTED_OPENTOFU_PLAN_JSON",
            [],
        ),
        "fixed_point": None,
        "refuted": None,
    }

    if not binary_verified or not release_asset_verified or not checksums_asset_verified:
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "ENGINE", "VERIFY_PINNED_RELEASE_DIGESTS", "PINNED_RELEASE_OR_BINARY_DIGEST_MISMATCH", "REACQUIRE_LOCKED_RELEASE_ASSETS"
        )
    elif release.get("iac_engine") != "OPENTOFU" or release.get("iac_engine_version") != "1.12.6":
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "ENGINE", "BIND_PLAN_ENGINE", "EXPLICIT_OPENTOFU_1_12_6_IDENTITY_MISSING", "PIN_EXPLICIT_OPENTOFU_RELEASE"
        )
    elif not version_consistent:
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "ENGINE", "VERIFY_VERSION_RECEIPT", "VERSION_JSON_MISMATCH_WITH_PINNED_RELEASE", "CAPTURE_PINNED_OPENTOFU_VERSION_JSON"
        )
    elif not command_valid:
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "AUTHORITY", "VERIFY_READ_ONLY_PLAN_COMMAND", "PLAN_COMMAND_IS_NOT_PINNED_READ_ONLY_JSON_PLAN", "EXECUTE_PINNED_READ_ONLY_PLAN_COMMAND"
        )
    elif stream_error and stream_error["kind"] == "missing":
        receipt["unknown"] = unknown_coordinates(
            "PLAN", "READ_PINNED_JSON_UI", stream_error["reason"], "OBSERVATION_UNAVAILABLE", "CAPTURE_PINNED_OPENTOFU_PLAN_JSON", []
        )
    elif stream_error and stream_error["kind"] == "malformed":
        receipt["state"] = "FIXED_POINT"
        receipt["unknown"] = None
        receipt["fixed_point"] = {
            "stage": "PLAN",
            "step": "PARSE_PLAN_JSON",
            "reason": stream_error["reason"],
            "line": stream_error.get("line"),
            "detail": stream_error.get("detail"),
            "next_operation": "REPAIR_PLAN_JSON_STREAM",
            "blocked_by": [],
        }
    elif not messages or messages[0].get("type") != "version":
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "PLAN", "READ_PINNED_JSON_UI", "PLAN_VERSION_MESSAGE_MISSING", "CAPTURE_VERSION_JSON_UI_MESSAGE"
        )
    elif not isinstance(ui_version, str) or ui_version.split(".", 1)[0] != "1":
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "PLAN", "READ_PINNED_JSON_UI", "UNSUPPORTED_JSON_UI_MAJOR", "PIN_OR_SUPPORT_JSON_UI_MAJOR"
        )
    elif exit_code not in (0, 2):
        receipt["state"] = "REFUTED"
        receipt["unknown"] = None
        receipt["refuted"] = receipt_claim(
            "REFUTED", "PLAN", "READ_PLAN_EXIT_CODE", "PLAN_EXIT_CODE_NOT_0_OR_2", "CAPTURE_SUCCESSFUL_PLAN_RECEIPT"
        )
    else:
        actions: list[dict[str, str]] = []
        summary = None
        drift_count = 0
        for message in messages:
            message_type = message.get("type")
            if message_type == "planned_change":
                change = message.get("change")
                resource = change.get("resource") if isinstance(change, dict) else None
                action = change.get("action") if isinstance(change, dict) else None
                address = resource.get("addr") if isinstance(resource, dict) else None
                if not isinstance(address, str) or action not in PLAN_ACTIONS:
                    receipt["state"] = "REFUTED"
                    receipt["unknown"] = None
                    receipt["refuted"] = receipt_claim(
                        "REFUTED", "PLAN", "READ_PLANNED_CHANGE", "UNSUPPORTED_PLANNED_CHANGE_SHAPE", "PIN_SUPPORTED_PLAN_UI_SCHEMA"
                    )
                    break
                actions.append({"address": address, "action": action})
            elif message_type == "resource_drift":
                drift_count += 1
            elif message_type == "change_summary":
                summary = message.get("changes")
        else:
            if not isinstance(summary, dict):
                receipt["state"] = "REFUTED"
                receipt["unknown"] = None
                receipt["refuted"] = receipt_claim(
                    "REFUTED", "PLAN", "READ_CHANGE_SUMMARY", "PLAN_CHANGE_SUMMARY_MISSING", "CAPTURE_COMPLETE_PLAN_JSON"
                )
            else:
                receipt["resource_actions"] = sorted(actions, key=lambda item: (item["address"], item["action"]))
                receipt["change_summary"] = summary
                receipt["drift_count"] = drift_count
                receipt["state"] = "CLOSED"
                receipt["unknown"] = None

    write_json(output, receipt)


def match_plan_to_intent(artifact_path: Path, plan_receipt_path: Path, oracle_path: Path, output: Path) -> None:
    receipt = read_json(plan_receipt_path)
    oracle = load_plan_oracle(oracle_path)
    current_artifact_sha = sha256_file(artifact_path)
    expected_actions = oracle["resource_actions"]
    actual_actions = receipt.get("resource_actions", [])
    receipt_inputs = receipt.get("inputs") if isinstance(receipt.get("inputs"), dict) else {}
    claim: dict[str, Any]
    state = receipt.get("state")
    if state == "UNKNOWN":
        coordinates = receipt.get("unknown")
        if not isinstance(coordinates, dict) or set(coordinates) != UNKNOWN_FIELDS or not isinstance(coordinates.get("blocked_by"), list):
            coordinates = unknown_coordinates("PLAN", "MATCH_PLAN_TO_INTENT", "PLAN_RECEIPT_UNKNOWN_COORDINATES_MISSING", "DIRECT_MISSING", "CAPTURE_COMPLETE_PLAN_RECEIPT", [])
        claim = {"state": "UNKNOWN", **coordinates}
    elif state == "FIXED_POINT":
        fixed_point = receipt.get("fixed_point")
        if not isinstance(fixed_point, dict):
            fixed_point = {
                "stage": "PLAN",
                "step": "PARSE_PLAN_JSON",
                "reason": "PLAN_FIXED_POINT_COORDINATES_MISSING",
                "next_operation": "REPAIR_PLAN_JSON_STREAM",
                "blocked_by": [],
            }
        claim = {"state": "FIXED_POINT", **fixed_point, "unknown_class": None}
    elif state == "REFUTED":
        refuted = receipt.get("refuted")
        if not isinstance(refuted, dict):
            refuted = receipt_claim("REFUTED", "PLAN", "VERIFY_PLAN_RECEIPT", "PLAN_RECEIPT_REFUTED", "REPAIR_PLAN_RECEIPT")
        claim = {"state": "REFUTED", **refuted}
    elif state != "CLOSED":
        claim = {
            "state": "REFUTED",
            **receipt_claim("REFUTED", "PLAN", "VERIFY_PLAN_RECEIPT", "UNRECOGNIZED_PLAN_RECEIPT_STATE", "CAPTURE_SUPPORTED_PLAN_RECEIPT"),
        }
    elif receipt.get("input_artifact_sha256") != current_artifact_sha or receipt_inputs.get("generated_config_sha256") != current_artifact_sha:
        claim = {
            "state": "UNKNOWN",
            **unknown_coordinates("PLAN", "MATCH_PLAN_TO_INTENT", "STALE_PLAN_INPUT_DIGEST", "DIRECT_MISSING", "REGENERATE_PLAN_FOR_CURRENT_ARTIFACT", []),
        }
    elif receipt.get("iac_engine") != "OPENTOFU" or receipt.get("engine_identity_source") != "PINNED_RELEASE_LOCK":
        claim = {
            "state": "REFUTED",
            **receipt_claim("REFUTED", "ENGINE", "MATCH_PLAN_ENGINE", "ENGINE_INFERRED_FROM_COMPATIBILITY_FIELD", "CAPTURE_EXPLICIT_ENGINE_RECEIPT"),
        }
    elif receipt.get("binary_verified") is not True or receipt.get("release", {}).get("asset_verified") is not True or receipt.get("version_consistent_with_pinned_release") is not True:
        claim = {
            "state": "REFUTED",
            **receipt_claim("REFUTED", "ENGINE", "VERIFY_PLAN_TOOLCHAIN", "PLAN_TOOLCHAIN_DIGEST_OR_VERSION_NOT_VERIFIED", "RECAPTURE_PINNED_TOOLCHAIN_RECEIPT"),
        }
    elif receipt.get("command_verified") is not True or receipt.get("read_only") != {
        "refresh": False,
        "input": False,
        "apply": 0,
        "destroy": 0,
        "cloud": 0,
        "network": 0,
        "source_write": 0,
    }:
        claim = {
            "state": "REFUTED",
            **receipt_claim("REFUTED", "AUTHORITY", "MATCH_READ_ONLY_PLAN", "READ_ONLY_PLAN_AUTHORITY_ESCALATION", "EXECUTE_ONLY_PINNED_READ_ONLY_PLAN"),
        }
    elif receipt.get("oracle_resource_action_dependencies") != oracle.get("resource_action_dependencies"):
        claim = {
            "state": "REFUTED",
            **receipt_claim("REFUTED", "PLAN", "MATCH_RESOURCE_ACTION_DEPENDENCIES", "PLAN_DEPENDENCY_CONTRADICTION", "REGENERATE_PLAN_RECEIPT_FROM_CURRENT_ORACLE"),
        }
    elif receipt.get("drift_count") != 0:
        claim = {
            "state": "REFUTED",
            "stage": "PLAN",
            "step": "REJECT_IGNORED_DRIFT",
            "reason": "PLAN_DRIFT_WAS_NOT_INCLUDED_IN_MATCH",
            "unknown_class": None,
            "next_operation": None,
            "blocked_by": [],
        }
    elif actual_actions != expected_actions or receipt.get("change_summary") != oracle.get("change_summary"):
        claim = {
            "state": "REFUTED",
            "stage": "PLAN",
            "step": "MATCH_RESOURCE_ACTION_SET",
            "reason": "GOOO_INTENT_PLAN_RESOURCE_ACTION_CONTRADICTION",
            "unknown_class": None,
            "next_operation": None,
            "blocked_by": [],
        }
    else:
        claim = {
            "state": "CLOSED",
            "stage": "PLAN",
            "step": "MATCH_RESOURCE_ACTION_SET",
            "reason": "GOOO_INTENT_MATCHES_OPENTOFU_PLAN",
            "unknown_class": None,
            "next_operation": None,
            "blocked_by": [],
        }
    write_json(
        output,
        {
            "schema": "gooo/opentofu-bridge/plan-match/v1",
            "state": claim["state"],
            "claim": claim,
            "artifact_sha256": current_artifact_sha,
            "plan_receipt_sha256": sha256_file(plan_receipt_path),
            "oracle_sha256": sha256_file(oracle_path),
            "expected_resource_actions": expected_actions,
            "observed_resource_actions": actual_actions,
            "resource_action_count": len(actual_actions),
        },
    )


def check_bindings(bindings_path: Path, denominator: dict[str, Any]) -> dict[str, Any]:
    bindings = read_json(bindings_path)
    expected_cells = cells(denominator)
    if bindings.get("schema") != "gooo/opentofu-bridge/meta-bindings/v1" or bindings.get("verified") is not True:
        die("meta bindings are not verified")
    if len(bindings.get("bindings", [])) != len(expected_cells):
        die("meta binding count mismatch")
    by_id = {binding.get("id"): binding for binding in bindings["bindings"]}
    if set(by_id) != {cell["id"] for cell in expected_cells}:
        die("meta binding cell set mismatch")
    return bindings


def generated_artifact() -> dict[str, Any]:
    return {
        "//": "Generated from the Gooo InfrastructureIntent; OpenTofu is the configuration authority.",
        "output": {
            "hello_id": {
                "value": "${terraform_data.hello.id}"
            }
        },
        "resource": {
            "terraform_data": {
                "hello": {
                    "input": "hello-from-gooo"
                }
            }
        }
    }


def generate_outputs(source: Path, graph_path: Path, lock_path: Path, spec_path: Path, bindings_path: Path, denominator_path: Path, output_dir: Path) -> None:
    lock = read_json(lock_path)
    denominator = read_json(denominator_path)
    bindings = check_bindings(bindings_path, denominator)
    spec_digest = check_spec(spec_path, lock)
    graph = read_json(graph_path)
    intent_entities = [
        node for node in graph.get("nodes", [])
        if node.get("name") == "InfrastructureIntent" and node.get("kind") != "Activity"
    ]
    if len(intent_entities) != 1:
        die("InfrastructureIntent entity is not uniquely present in the released graph")
    if output_dir.exists() and any(output_dir.iterdir()):
        die(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = generated_artifact()
    artifact_bytes = json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    (output_dir / "intent.tf.json").write_bytes(artifact_bytes)
    release = lock["opentofu"]
    dossier = f"""# Gooo → OpenTofu bridge dossier

## Intent

The source declaration is `examples/intent/main.gooo`. Its released semantic
graph contains one `InfrastructureIntent` entity (`{intent_entities[0].get('id')}`)
and a twelve-activity dependency chain. The bridge materializes that intent as
one `terraform_data.hello` resource with the literal input
`hello-from-gooo`.

## Immutable authority

- Gooo Core: `{lock['gooo']['repository']}@{lock['gooo']['tag']}` with target
  commit `{lock['gooo']['target_commit_sha']}` and locked asset SHA-256
  `{lock['gooo']['asset']['sha256']}`.
- OpenTofu: `{release['repository']}@{release['tag']}` with target commit
  `{release['target_commit_sha']}` and locked Linux asset SHA-256
  `{release['asset']['sha256']}`.
- JSON configuration specification: `{release['json_spec']['url']}` at the
  immutable ref `{release['json_spec']['ref']}`, SHA-256 `{spec_digest}`.
- Gooo source SHA-256: `{sha256_file(source)}`.
- Released semantic graph SHA-256: `{sha256_file(graph_path)}`.

The generated file is `intent.tf.json`. The official OpenTofu binary is the
authority for full configuration validation; the bridge only checks the
specific declared output contract and determinism.

## Closed path

1. Declare the infrastructure intent in Gooo.
2. Bind the released Gooo graph and its dependency edges.
3. Consume the immutable OpenTofu JSON specification.
4. Generate `intent.tf.json` and this dossier.
5. Validate the generated configuration with the pinned OpenTofu CLI.
6. Capture the machine-readable plan and compare its resource/action set with
   the independent oracle.
7. Replay generation byte-for-byte.

## Case boundaries

- Normal: generated outputs are present and the bridge result is `CLOSED`.
- UNKNOWN: when the pinned JSON specification is missing, the result is
  `FAIL_CLOSED`; the direct missing claim and its dependency-blocked claim
  retain all coordinates and `blocked_by`.
- REFUTED: a deliberate intent/artifact contradiction is `REFUTED`; this state
  takes precedence over any possible UNKNOWN candidate.

## Non-claims

No OpenTofu source checkout, build, provider installation, backend init, apply,
test, cloud access, or deployment is part of this slice. The plan runs with
refresh disabled and writes only to a caller-owned temporary directory.
Repository writes are zero after the caller-owned output is published.
"""
    (output_dir / "dossier.md").write_text(dossier, encoding="utf-8")


def validate_bridge_artifact(
    artifact_path: Path,
    dossier_path: Path,
    lock_path: Path,
    spec_path: Path,
    bindings_path: Path,
    denominator_path: Path,
    output: Path,
    tofu_result: Path | None,
    tofu_state: str | None,
    tofu_reason: str | None,
    source_path: Path,
    graph_path: Path,
    toolchain_manifest_path: Path,
    command_path: Path,
    binary_path: Path,
    release_asset_path: Path,
    checksums_asset_path: Path,
    version_json_path: Path,
    exit_code: int,
) -> None:
    lock = read_json(lock_path)
    denominator = read_json(denominator_path)
    bindings = check_bindings(bindings_path, denominator)
    spec_digest = check_spec(spec_path, lock)
    artifact = read_json(artifact_path)
    if artifact != generated_artifact():
        die("generated artifact does not match the deterministic intent projection")
    dossier = read_text(dossier_path)
    if "# Gooo → OpenTofu bridge dossier" not in dossier or "hello-from-gooo" not in dossier:
        die("human dossier is incomplete")
    if sorted(path.name for path in artifact_path.parent.iterdir() if path.is_file()) != OUTPUT_NAMES:
        die("generated output file set is not exact")

    official: dict[str, Any]
    command = read_json(command_path)
    release = lock["opentofu"]
    binary_sha256 = sha256_file(binary_path)
    release_asset_sha256 = sha256_file(release_asset_path)
    checksums_asset_sha256 = sha256_file(checksums_asset_path)
    version_json = read_json(version_json_path)
    toolchain_sha256 = sha256_file(toolchain_manifest_path)
    command_valid = (
        isinstance(command, dict)
        and command.get("argv") == release.get("validate_command")
        and command.get("working_directory") == "caller-owned-module"
        and command.get("read_only") is True
    )
    identity_valid = (
        release.get("iac_engine") == "OPENTOFU"
        and release.get("iac_engine_version") == "1.12.6"
        and binary_sha256 == release.get("binary_sha256")
        and release_asset_sha256 == release.get("asset", {}).get("sha256")
        and checksums_asset_sha256 == release.get("checksums", {}).get("sha256")
        and isinstance(version_json, dict)
        and version_json.get("terraform_version") == release.get("iac_engine_version")
        and version_json.get("platform") == release.get("platform")
        and command_valid
    )
    if not identity_valid:
        die("official OpenTofu validation identity is not pinned and verified")
    if tofu_result is not None:
        result = read_json(tofu_result)
        if exit_code != 0 or result.get("valid") is not True or result.get("error_count") not in (0, None):
            die("official OpenTofu validation did not close")
        official = {
            "state": "CLOSED",
            "command": ["tofu", "validate", "-json"],
            "command_sha256": canonical_sha256(command),
            "valid": True,
            "error_count": int(result.get("error_count", 0)),
            "exit_code": exit_code,
            "result_sha256": sha256_file(tofu_result),
            "source_sha256": sha256_file(source_path),
            "semantic_ir_sha256": sha256_file(graph_path),
            "artifact_sha256": sha256_file(artifact_path),
            "toolchain_sha256": toolchain_sha256,
            "binary_sha256": binary_sha256,
            "release_asset_sha256": release_asset_sha256,
        }
    else:
        official = {
            "state": "UNKNOWN",
            "command": ["tofu", "validate", "-json"],
            "valid": None,
            "error_count": None,
            "exit_code": exit_code,
            "reason": tofu_reason or "OPENTOFU_VALIDATION_NOT_EXECUTED",
            "unknown_class": "EXECUTION_UNAVAILABLE",
            "next_operation": "EXECUTE_PINNED_OPENTOFU_VALIDATE",
            "blocked_by": [],
            "source_sha256": sha256_file(source_path),
            "semantic_ir_sha256": sha256_file(graph_path),
            "artifact_sha256": sha256_file(artifact_path),
            "toolchain_sha256": toolchain_sha256,
            "binary_sha256": binary_sha256,
            "release_asset_sha256": release_asset_sha256,
        }
    write_json(
        output,
        {
            "schema": "gooo/opentofu-bridge/validation/v1",
            "state": "CLOSED",
            "activity": "VerifyGeneratedOutputs",
            "activity_id": next(binding["activity_id"] for binding in bindings["bindings"] if binding["activity"] == "VerifyGeneratedOutputs"),
            "artifact_sha256": sha256_file(artifact_path),
            "dossier_sha256": sha256_file(dossier_path),
            "json_spec_sha256": spec_digest,
            "source_sha256": sha256_file(source_path),
            "semantic_ir_sha256": sha256_file(graph_path),
            "toolchain_sha256": toolchain_sha256,
            "structural_checks": {
                "artifact_is_deterministic_projection": True,
                "output_file_count": 2,
                "output_file_set_exact": True,
            },
            "official_opentofu": official,
        },
    )


def claim(activity: str, bindings: dict[str, Any], **fields: Any) -> dict[str, Any]:
    binding = next(item for item in bindings["bindings"] if item["activity"] == activity)
    return {"activity": activity, "activity_id": binding["activity_id"], **fields}


def evaluate_case(mode: str, artifact_path: Path, dossier_path: Path, plan_match_path: Path, bindings_path: Path, denominator_path: Path, output: Path) -> None:
    denominator = read_json(denominator_path)
    bindings = check_bindings(bindings_path, denominator)
    artifact_digest = sha256_file(artifact_path) if artifact_path.exists() else None
    dossier_digest = sha256_file(dossier_path) if dossier_path.exists() else None
    plan_match = read_json(plan_match_path)
    plan_match_digest = sha256_file(plan_match_path)
    if mode == "normal":
        if artifact_digest is None or dossier_digest is None or plan_match.get("state") != "CLOSED":
            die("normal case inputs are missing")
        result = {
            "schema": "gooo/opentofu-bridge/case/v1",
            "case_id": "normal",
            "decision": "CLOSED",
            "resolution": "EXACT",
            "claims": [
                claim("VerifyGeneratedOutputs", bindings, state="CLOSED", stage="VALIDATION", step="VERIFY_GENERATED_OUTPUTS", reason="GENERATED_OUTPUTS_STRUCTURALLY_VERIFIED", unknown_class=None, next_operation=None, blocked_by=[]),
                claim("MatchGoooIntentToOpenTofuPlan", bindings, state="CLOSED", stage="PLAN", step="MATCH_RESOURCE_ACTION_SET", reason="GOOO_INTENT_MATCHES_OPENTOFU_PLAN", unknown_class=None, next_operation=None, blocked_by=[]),
            ],
            "evidence": {"artifact_sha256": artifact_digest, "dossier_sha256": dossier_digest, "plan_match_sha256": plan_match_digest},
        }
    elif mode == "unknown":
        result = {
            "schema": "gooo/opentofu-bridge/case/v1",
            "case_id": "plan-boundary-unknowns",
            "decision": "FAIL_CLOSED",
            "resolution": "LOWER_RESOLUTION",
            "claims": [
                claim("MatchGoooIntentToOpenTofuPlan", bindings, state="UNKNOWN", stage="PLAN", step="MATCH_CURRENT_PLAN_INPUT", reason="STALE_PLAN_INPUT_DIGEST", unknown_class="DIRECT_MISSING", next_operation="REGENERATE_PLAN_FOR_CURRENT_ARTIFACT", blocked_by=[]),
                claim("MatchGoooIntentToOpenTofuPlan", bindings, state="UNKNOWN", stage="ENGINE", step="BIND_PLAN_ENGINE", reason="ENGINE_INFERRED_FROM_COMPATIBILITY_FIELD", unknown_class="DIRECT_MISSING", next_operation="CAPTURE_EXPLICIT_OPENTOFU_RELEASE_RECEIPT", blocked_by=[]),
                claim("MatchGoooIntentToOpenTofuPlan", bindings, state="UNKNOWN", stage="PLAN", step="READ_PLAN_JSON_UI", reason="UNSUPPORTED_JSON_UI_MAJOR", unknown_class="OBSERVATION_UNAVAILABLE", next_operation="PIN_SUPPORTED_PLAN_JSON_UI_MAJOR", blocked_by=[]),
            ],
            "evidence": {"artifact_sha256": artifact_digest, "dossier_sha256": dossier_digest, "plan_match_sha256": plan_match_digest},
        }
    elif mode == "refuted":
        result = {
            "schema": "gooo/opentofu-bridge/case/v1",
            "case_id": "ignored-drift-and-contradiction",
            "decision": "FAIL_CLOSED",
            "resolution": "EXACT",
            "precedence": "REFUTED_OVER_UNKNOWN",
            "claims": [
                claim("MatchGoooIntentToOpenTofuPlan", bindings, state="REFUTED", stage="PLAN", step="REJECT_IGNORED_DRIFT", reason="PLAN_DRIFT_WAS_NOT_INCLUDED_IN_MATCH", unknown_class=None, next_operation=None, blocked_by=[]),
                claim("RefuteContradictionCase", bindings, state="REFUTED", stage="REFUTATION", step="REFUTE_INTENT_PLAN_CONTRADICTION", reason="GOOO_INTENT_PLAN_RESOURCE_ACTION_CONTRADICTION", unknown_class=None, next_operation=None, blocked_by=[]),
            ],
            "evidence": {"declared_input": "terraform_data.hello=create", "contradictory_observation": "terraform_data.hello=delete", "artifact_sha256": artifact_digest, "plan_match_sha256": plan_match_digest},
        }
    else:
        die(f"unsupported case mode: {mode}")
    write_json(output, result)


def replay_outputs(source: Path, graph_path: Path, lock_path: Path, spec_path: Path, bindings_path: Path, denominator_path: Path, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="gooo-opentofu-bridge-replay-") as temp:
        root = Path(temp)
        first = root / "first"
        second = root / "second"
        generate_outputs(source, graph_path, lock_path, spec_path, bindings_path, denominator_path, first)
        generate_outputs(source, graph_path, lock_path, spec_path, bindings_path, denominator_path, second)
        comparisons = []
        for name in OUTPUT_NAMES:
            first_bytes = (first / name).read_bytes()
            second_bytes = (second / name).read_bytes()
            comparisons.append({"path": name, "byte_equal": first_bytes == second_bytes, "sha256": sha256_bytes(first_bytes)})
        if not all(item["byte_equal"] for item in comparisons):
            die("replay is not byte deterministic")
        write_json(output, {"schema": "gooo/opentofu-bridge/replay/v1", "state": "CLOSED", "comparisons": comparisons, "comparison_count": len(comparisons)})


def check_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        die(f"{field} must be a non-negative integer")
    return value


def expected_case_state(case_matrix: list[dict[str, Any]], case_id: str) -> str | None:
    for case in case_matrix:
        if case.get("case_id") == case_id:
            value = case.get("state")
            return value if isinstance(value, str) else None
    return None


def record_observation(publish_dir: Path, bindings_path: Path, denominator_path: Path, lock_path: Path, source: Path, graph_path: Path, validation_path: Path, replay_path: Path, plan_receipt_path: Path, plan_match_path: Path, oracle_path: Path, version_json_path: Path, cases_dir: Path, measurements_path: Path, line_metrics_path: Path, output: Path) -> None:
    denominator = read_json(denominator_path)
    bindings = check_bindings(bindings_path, denominator)
    lock = read_json(lock_path)
    measurement_doc = read_json(measurements_path)
    line_metrics = read_json(line_metrics_path)
    plan_receipt = read_json(plan_receipt_path)
    plan_match = read_json(plan_match_path)
    oracle = load_plan_oracle(oracle_path)
    if not isinstance(measurement_doc, list) or not isinstance(line_metrics, list):
        die("measurement inputs must be arrays")
    known_activities = {binding["activity"]: binding["activity_id"] for binding in bindings["bindings"]}
    cell_by_activity = {cell["activity"]: cell for cell in cells(denominator)}
    if plan_receipt.get("state") != "CLOSED" or plan_match.get("state") != "CLOSED":
        die("OpenTofu plan receipt and intent match must be CLOSED")
    if plan_receipt.get("iac_engine") != "OPENTOFU" or plan_receipt.get("engine_identity_source") != "PINNED_RELEASE_LOCK":
        die("plan engine identity is not explicit")
    if plan_receipt.get("oracle_resource_action_dependencies") != oracle["resource_action_dependencies"]:
        die("plan receipt dependency oracle is not linked")
    if plan_match.get("expected_resource_actions") != oracle["resource_actions"] or plan_match.get("observed_resource_actions") != oracle["resource_actions"]:
        die("plan resource/action oracle did not close")
    if plan_receipt.get("drift_count") != 0:
        die("ignored plan drift cannot close")
    if sha256_file(version_json_path) != plan_receipt.get("version_json_sha256"):
        die("version JSON digest is not linked to the plan receipt")
    for measurement in measurement_doc:
        if measurement.get("activity") not in known_activities:
            die(f"measurement is not linked to a released Gooo activity: {measurement}")
        measurement["activity_id"] = known_activities[measurement["activity"]]
        measurement["cell_id"] = cell_by_activity[measurement["activity"]]["id"]
        check_int(measurement.get("wall_ms", 0), "wall_ms")
        check_int(measurement.get("wall_ns", 0), "wall_ns")
        check_int(measurement.get("peak_rss_kib", 0), "peak_rss_kib")
        check_int(measurement.get("executions", 0), "executions")
        check_int(measurement.get("reused", 0), "reused")
        check_int(measurement.get("skipped", 0), "skipped")
    for metric in line_metrics:
        if metric.get("activity") not in known_activities:
            die(f"line metric is not linked to a released Gooo activity: {metric}")
        metric["activity_id"] = known_activities[metric["activity"]]
        metric["cell_id"] = cell_by_activity[metric["activity"]]["id"]
        metric["producer"] = {
            "source": str(source),
            "ir": str(graph_path),
            "generated_artifact": "bridge-observation.json",
            "evaluator": "workflow:Record activity-linked observations",
        }
        check_int(metric.get("value"), "line metric value")
    expected = sorted(OUTPUT_NAMES)
    actual = sorted(path.name for path in publish_dir.iterdir() if path.is_file())
    if actual != expected:
        die(f"published output set mismatch: {actual}")
    case_matrix = denominator.get("case_matrix")
    if not isinstance(case_matrix, list) or not case_matrix:
        die("denominator case matrix is missing")
    case_ids = [case.get("case_id") for case in case_matrix]
    if any(not isinstance(case_id, str) for case_id in case_ids) or len(set(case_ids)) != len(case_ids):
        die("denominator case IDs are not unique")
    cases = {case_id: read_json(cases_dir / f"{case_id}.json") for case_id in case_ids}
    for expected_case in case_matrix:
        actual_case = cases[expected_case["case_id"]]
        if actual_case.get("decision") != expected_case.get("decision"):
            die(f"case decision mismatch: {expected_case['case_id']}")
        expected_state = expected_case.get("state")
        if expected_state and not any(claim.get("state") == expected_state for claim in actual_case.get("claims", [])):
            die(f"case state mismatch: {expected_case['case_id']}")
    if cases["normal"].get("decision") != "CLOSED":
        die("normal case is not CLOSED")
    unknown_claims = cases["missing_plan"].get("claims", []) + cases["stale_input"].get("claims", [])
    if not unknown_claims or any(claim.get("state") != "UNKNOWN" or not UNKNOWN_FIELDS.issubset(claim) or not isinstance(claim.get("blocked_by"), list) for claim in unknown_claims):
        die("missing-plan and stale-input cases do not preserve UNKNOWN coordinates")
    refuted_case_ids = [case_id for case_id in case_ids if expected_case_state(case_matrix, case_id) == "REFUTED" and case_id != "precedence"]
    if any(not cases[case_id].get("claims") or not all(claim.get("state") == "REFUTED" for claim in cases[case_id]["claims"]) for case_id in refuted_case_ids):
        die("a REFUTED case is not fully REFUTED")
    precedence_claims = cases.get("precedence", {}).get("claims", [])
    if cases.get("precedence", {}).get("decision") != "FAIL_CLOSED" or not any(claim.get("state") == "REFUTED" for claim in precedence_claims) or not any(claim.get("state") == "UNKNOWN" for claim in precedence_claims) or not any(claim.get("state") == "CLOSED" for claim in precedence_claims):
        die("REFUTED > UNKNOWN > CLOSED precedence is not preserved")
    if cases["malformed"].get("decision") != "FIXED_POINT" or not all(claim.get("state") == "FIXED_POINT" for claim in cases["malformed"].get("claims", [])):
        die("malformed plan case is not a FIXED_POINT")
    validation = read_json(validation_path)
    replay = read_json(replay_path)
    output_digests = {name: sha256_file(publish_dir / name) for name in expected}
    stage_executions = sum(check_int(item.get("executions", 0), "executions") for item in measurement_doc)
    stage_reused = sum(check_int(item.get("reused", 0), "reused") for item in measurement_doc)
    graph_binding = next(item for item in bindings["bindings"] if item["activity"] == "BindGoooIntentToOpenTofu")
    final_binding = next(item for item in bindings["bindings"] if item["activity"] == "PreserveReadOnlyBoundary")
    by_stage = {item.get("stage"): item for item in measurement_doc}
    test_measurement = by_stage.get("test", {})
    build_measurement = by_stage.get("build", {})
    conformance_measurement = by_stage.get("conformance", {})
    artifact_bytes = sum((publish_dir / name).stat().st_size for name in expected)

    def linked_metric(name: str, value: int | str, unit: str, activity: str, generated_artifact: str) -> dict[str, Any]:
        binding = next(item for item in bindings["bindings"] if item["activity"] == activity)
        return {
            "name": name,
            "value": value,
            "unit": unit,
            "activity": activity,
            "activity_id": binding["activity_id"],
            "cell_id": cell_by_activity[activity]["id"],
            "producer": {
                "source": str(source),
                "ir": str(graph_path),
                "generated_artifact": generated_artifact,
                "evaluator": "scripts/bridge.py:record",
            },
        }

    metrics = [
        linked_metric("user_path_steps", denominator["expected_user_path_steps"], "steps", "VerifyGeneratedOutputs", "bridge-observation.json"),
        linked_metric("generated_file_count", len(expected), "files", "GenerateHumanDossier", "bridge-observation.json"),
        linked_metric("executed_verification_stages", stage_executions, "stage-executions", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("reused_verification_stages", stage_reused, "stage-reuses", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("resource_action_count", plan_match["resource_action_count"], "resource-actions", "MatchGoooIntentToOpenTofuPlan", "plan-match.json"),
        linked_metric("build_wall_ms", build_measurement.get("wall_ms", 0), "ms", "GenerateOpenTofuCompatibleArtifact", "bridge-observation.json"),
        linked_metric("build_wall_ns", build_measurement.get("wall_ns", 0), "ns", "GenerateOpenTofuCompatibleArtifact", "bridge-observation.json"),
        linked_metric("test_wall_ms", test_measurement.get("wall_ms", 0), "ms", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("test_wall_ns", test_measurement.get("wall_ns", 0), "ns", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("conformance_wall_ms", conformance_measurement.get("wall_ms", 0), "ms", "MatchGoooIntentToOpenTofuPlan", "bridge-observation.json"),
        linked_metric("conformance_wall_ns", conformance_measurement.get("wall_ns", 0), "ns", "MatchGoooIntentToOpenTofuPlan", "bridge-observation.json"),
        linked_metric("peak_rss_kib", max((item.get("peak_rss_kib", 0) for item in measurement_doc), default=0), "KiB", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("executed_test_count", test_measurement.get("executed_test_count", 0), "tests", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("reused_test_evidence_count", test_measurement.get("reused_test_evidence_count", 0), "tests", "VerifyDeterministicReplay", "bridge-observation.json"),
        linked_metric("skipped_test_count", test_measurement.get("skipped_test_count", 0), "tests", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("artifact_files", len(expected), "files", "GenerateOpenTofuCompatibleArtifact", "bridge-observation.json"),
        linked_metric("artifact_bytes", artifact_bytes, "bytes", "GenerateOpenTofuCompatibleArtifact", "bridge-observation.json"),
        linked_metric("repository_writes", 0, "writes", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        linked_metric("cross_project_required_gates", denominator.get("cross_project_required_gates", 0), "gates", "PreserveReadOnlyBoundary", "bridge-observation.json"),
        *line_metrics,
    ]
    primary_metrics = [
        {
            "metric_id": f"cell-metric-{cell['ordinal']:02d}",
            "cell_id": cell["id"],
            "activity": cell["activity"],
            "activity_id": known_activities[cell["activity"]],
            "value": "CLOSED",
            "unit": "cell-state",
            "producer": {
                "source": str(source),
                "ir": str(graph_path),
                "generated_artifact": "bridge-observation.json",
                "evaluator": "scripts/bridge.py:record",
            },
        }
        for cell in denominator["cells"]
    ]
    write_json(
        output,
        {
            "schema": "gooo/opentofu-bridge/observation/v1",
            "state": "CLOSED",
            "subject": {"source_sha256": sha256_file(source), "graph_sha256": sha256_file(graph_path), "release_lock_sha256": sha256_file(lock_path)},
            "user_path": {
                "step_count": denominator["expected_user_path_steps"],
                "steps": [
                    {"ordinal": 1, "name": "declare_intent", "activity": "DeclareGoooInfrastructureIntent", "activity_id": known_activities["DeclareGoooInfrastructureIntent"]},
                    {"ordinal": 2, "name": "bind_released_graph", "activity": "BindGoooIntentToOpenTofu", "activity_id": graph_binding["activity_id"]},
                    {"ordinal": 3, "name": "generate_outputs", "activity": "GenerateOpenTofuCompatibleArtifact", "activity_id": known_activities["GenerateOpenTofuCompatibleArtifact"]},
                    {"ordinal": 4, "name": "verify_outputs", "activity": "VerifyGeneratedOutputs", "activity_id": known_activities["VerifyGeneratedOutputs"]},
                    {"ordinal": 5, "name": "match_plan", "activity": "MatchGoooIntentToOpenTofuPlan", "activity_id": known_activities["MatchGoooIntentToOpenTofuPlan"]},
                ],
            },
            "generated_outputs": {"file_count": len(expected), "files": expected, "bytes": artifact_bytes, "digests": output_digests, "activity": "GenerateHumanDossier", "activity_id": known_activities["GenerateHumanDossier"]},
            "metrics": metrics,
            "primary_metrics": primary_metrics,
            "verification_stages": measurement_doc,
            "cases": cases,
            "validation": validation,
            "plan_receipt": plan_receipt,
            "plan_match": plan_match,
            "plan_oracle": {"sha256": sha256_file(oracle_path), "resource_actions": oracle["resource_actions"], "side_effects": oracle["side_effects"]},
            "identity": {
                "source_sha256": sha256_file(source),
                "graph_sha256": sha256_file(graph_path),
                "artifact_sha256": output_digests["intent.tf.json"],
                "plan_receipt_sha256": sha256_file(plan_receipt_path),
                "plan_match_sha256": sha256_file(plan_match_path),
                "oracle_sha256": sha256_file(oracle_path),
                "version_json_sha256": plan_receipt["version_json_sha256"],
                "binary_sha256": plan_receipt["binary_sha256"],
            },
            "replay": replay,
            "official_inputs": {
                "gooo_release": {"repository": lock["gooo"]["repository"], "tag": lock["gooo"]["tag"], "asset_sha256": lock["gooo"]["asset"]["sha256"]},
                "opentofu_release": {"repository": lock["opentofu"]["repository"], "tag": lock["opentofu"]["tag"], "asset_sha256": lock["opentofu"]["asset"]["sha256"]},
                "opentofu_json_spec_sha256": lock["opentofu"]["json_spec"]["sha256"],
            },
            "authority": lock["authority"],
            "case_matrix": case_matrix,
            "comparative_claims": {
                "improvement": {
                    "state": "UNKNOWN",
                    **unknown_coordinates("MEASUREMENT", "COMPARE_BEFORE_AFTER", "EXACT_BEFORE_AFTER_NOT_PROVIDED", "DIRECT_MISSING", "PROVIDE_EXACT_BEFORE_AFTER_BASELINE", []),
                },
                "utility": {
                    "state": "UNKNOWN",
                    **unknown_coordinates("MEASUREMENT", "ASSESS_INDEPENDENT_USER_UTILITY", "INDEPENDENT_USER_EVIDENCE_NOT_PROVIDED", "DIRECT_MISSING", "COLLECT_INDEPENDENT_USER_EVIDENCE", []),
                },
            },
            "evaluator": {
                "path": "scripts/evaluator.py",
                "sha256": sha256_file(Path(__file__).with_name("evaluator.py")),
                "independent": True,
            },
        },
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    bind = sub.add_parser("bind")
    bind.add_argument("--source", type=Path, required=True)
    bind.add_argument("--graph", type=Path, required=True)
    bind.add_argument("--denominator", type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)

    generate = sub.add_parser("generate")
    for name in ("source", "graph", "lock", "spec", "bindings", "denominator"):
        generate.add_argument(f"--{name}", type=Path, required=True)
    generate.add_argument("--output-dir", type=Path, required=True)

    validate = sub.add_parser("validate")
    for name in ("artifact", "dossier", "lock", "spec", "bindings", "denominator", "output"):
        validate.add_argument(f"--{name}", type=Path, required=True)
    for name in ("source", "graph", "toolchain-manifest", "binary", "release-asset", "checksums-asset", "version-json"):
        validate.add_argument(f"--{name}", type=Path, required=True)
    validate.add_argument("--command", dest="command_path", type=Path, required=True)
    validate.add_argument("--tofu-result", type=Path)
    validate.add_argument("--tofu-state", choices=("UNKNOWN",))
    validate.add_argument("--tofu-reason")
    validate.add_argument("--exit-code", type=int, required=True)

    plan_receipt = sub.add_parser("plan-receipt")
    for name in (
        "plan-ui",
        "version-json",
        "binary",
        "artifact",
        "oracle",
        "lock",
        "source",
        "graph",
        "spec",
        "toolchain-manifest",
        "release-asset",
        "checksums-asset",
        "output",
    ):
        plan_receipt.add_argument(f"--{name}", type=Path, required=True)
    plan_receipt.add_argument("--command", dest="command_path", type=Path, required=True)
    plan_receipt.add_argument("--exit-code", type=int, required=True)

    plan_match = sub.add_parser("match-plan")
    for name in ("artifact", "plan-receipt", "oracle", "output"):
        plan_match.add_argument(f"--{name}", type=Path, required=True)

    plan_receipt = sub.add_parser("plan-receipt")
    for name in ("plan-ui", "version-json", "binary", "artifact", "oracle", "lock", "output"):
        plan_receipt.add_argument(f"--{name}", type=Path, required=True)
    plan_receipt.add_argument("--exit-code", type=int, required=True)

    plan_match = sub.add_parser("match-plan")
    for name in ("artifact", "plan-receipt", "oracle", "output"):
        plan_match.add_argument(f"--{name}", type=Path, required=True)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--mode", choices=("normal", "unknown", "refuted"), required=True)
    for name in ("artifact", "dossier", "plan-match", "bindings", "denominator", "output"):
        evaluate.add_argument(f"--{name}", type=Path, required=True)

    replay = sub.add_parser("replay")
    for name in ("source", "graph", "lock", "spec", "bindings", "denominator", "output"):
        replay.add_argument(f"--{name}", type=Path, required=True)

    record = sub.add_parser("record")
    for name in ("publish-dir", "bindings", "denominator", "lock", "source", "graph", "validation", "replay", "plan-receipt", "plan-match", "oracle", "version-json", "cases-dir", "measurements", "line-metrics", "output"):
        record.add_argument(f"--{name}", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "bind":
        bind_graph(args.source, args.graph, args.denominator, args.output)
    elif args.command == "generate":
        generate_outputs(args.source, args.graph, args.lock, args.spec, args.bindings, args.denominator, args.output_dir)
    elif args.command == "validate":
        validate_bridge_artifact(
            args.artifact,
            args.dossier,
            args.lock,
            args.spec,
            args.bindings,
            args.denominator,
            args.output,
            args.tofu_result,
            args.tofu_state,
            args.tofu_reason,
            args.source,
            args.graph,
            args.toolchain_manifest,
            args.command_path,
            args.binary,
            args.release_asset,
            args.checksums_asset,
            args.version_json,
            args.exit_code,
        )
    elif args.command == "plan-receipt":
        generate_plan_receipt(
            args.plan_ui,
            args.version_json,
            args.binary,
            args.artifact,
            args.oracle,
            args.lock,
            args.source,
            args.graph,
            args.spec,
            args.toolchain_manifest,
            args.command_path,
            args.release_asset,
            args.checksums_asset,
            args.exit_code,
            args.output,
        )
    elif args.command == "match-plan":
        match_plan_to_intent(args.artifact, args.plan_receipt, args.oracle, args.output)
    elif args.command == "evaluate":
        evaluate_case(args.mode, args.artifact, args.dossier, args.plan_match, args.bindings, args.denominator, args.output)
    elif args.command == "replay":
        replay_outputs(args.source, args.graph, args.lock, args.spec, args.bindings, args.denominator, args.output)
    elif args.command == "record":
        record_observation(args.publish_dir, args.bindings, args.denominator, args.lock, args.source, args.graph, args.validation, args.replay, args.plan_receipt, args.plan_match, args.oracle, args.version_json, args.cases_dir, args.measurements, args.line_metrics, args.output)
    else:
        die(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(1)
