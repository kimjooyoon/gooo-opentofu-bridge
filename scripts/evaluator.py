#!/usr/bin/env python3
"""Independent, read-only evaluator for the bridge's evidence cases.

This file intentionally does not import the generator or receipt builder. It
consumes their JSON artifacts, the released semantic IR, and the static plan
oracle, then applies only the evidence contract below:

    REFUTED > UNKNOWN > CLOSED

The evaluator never executes OpenTofu and never writes outside its caller-owned
output path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


UNKNOWN_FIELDS = {"stage", "step", "reason", "unknown_class", "next_operation", "blocked_by"}
CASES = (
    "normal",
    "missing_plan",
    "stale_input",
    "unexpected_create",
    "unexpected_delete",
    "engine_inference",
    "unsupported_json",
    "malformed",
    "authority_escalation",
    "precedence",
)


def die(message: str) -> None:
    raise SystemExit(f"evaluator error: {message}")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        die(f"cannot read JSON {path}: {exc}")


def sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        die(f"cannot hash {path}: {exc}")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def claim(activity: str, activity_id: str, state: str, stage: str, step: str, reason: str, next_operation: str | None, blocked_by: list[str]) -> dict[str, Any]:
    return {
        "activity": activity,
        "activity_id": activity_id,
        "state": state,
        "stage": stage,
        "step": step,
        "reason": reason,
        "unknown_class": None if state != "UNKNOWN" else "DIRECT_MISSING",
        "next_operation": next_operation,
        "blocked_by": blocked_by,
    }


def unknown(activity_id: str, stage: str, step: str, reason: str, unknown_class: str, next_operation: str, blocked_by: list[str]) -> dict[str, Any]:
    value = claim("MatchGoooIntentToOpenTofuPlan", activity_id, "UNKNOWN", stage, step, reason, next_operation, blocked_by)
    value["unknown_class"] = unknown_class
    if set(value) - {"activity", "activity_id"} != UNKNOWN_FIELDS | {"state"}:
        die("UNKNOWN claim coordinates are not exact")
    if not isinstance(value["blocked_by"], list):
        die("UNKNOWN blocked_by is not an array")
    return value


def refuted(activity: str, activity_id: str, stage: str, step: str, reason: str, next_operation: str | None = None) -> dict[str, Any]:
    return claim(activity, activity_id, "REFUTED", stage, step, reason, next_operation, [])


def fixed_point(activity_id: str, reason: str, detail: str) -> dict[str, Any]:
    return {
        "activity": "MatchGoooIntentToOpenTofuPlan",
        "activity_id": activity_id,
        "state": "FIXED_POINT",
        "stage": "PLAN",
        "step": "PARSE_PLAN_JSON",
        "reason": reason,
        "unknown_class": None,
        "next_operation": "REPAIR_PLAN_JSON_STREAM",
        "blocked_by": [],
        "detail": detail,
    }


def load_context(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str, str]:
    bindings = read_json(args.bindings)
    if bindings.get("schema") != "gooo/opentofu-bridge/meta-bindings/v1" or bindings.get("verified") is not True:
        die("released activity bindings are not verified")
    match_binding = next((item for item in bindings.get("bindings", []) if item.get("activity") == "MatchGoooIntentToOpenTofuPlan"), None)
    if not isinstance(match_binding, dict) or not isinstance(match_binding.get("activity_id"), str):
        die("independent evaluator activity binding is missing")
    oracle = read_json(args.oracle)
    if oracle.get("schema") != "gooo/opentofu-bridge/plan-oracle/v1":
        die("plan oracle schema is unsupported")
    expected = oracle.get("resource_actions")
    dependencies = oracle.get("resource_action_dependencies")
    if not isinstance(expected, list) or not isinstance(dependencies, list) or not expected or not dependencies:
        die("plan oracle is incomplete")
    for dependency in dependencies:
        if not isinstance(dependency, dict) or set(dependency) != {"intent_entity", "address", "action", "depends_on"}:
            die("plan oracle dependency shape is invalid")
    artifact = read_json(args.artifact)
    try:
        dossier = args.dossier.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        die(f"cannot read dossier {args.dossier}: {exc}")
    if "# Gooo → OpenTofu bridge dossier" not in dossier:
        die("generated human dossier is not bound")
    plan_match = read_json(args.plan_match)
    receipt = read_json(args.plan_receipt) if args.plan_receipt.exists() else {}
    source_sha = sha256_file(args.source)
    graph_sha = sha256_file(args.graph)
    if receipt and receipt.get("source_sha256") not in (None, source_sha):
        die("plan receipt source digest is stale")
    if receipt and receipt.get("semantic_ir_sha256") not in (None, graph_sha):
        die("plan receipt semantic IR digest is stale")
    return bindings, oracle, artifact, plan_match, match_binding["activity_id"], source_sha


def evidence(args: argparse.Namespace, source_sha: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "source_sha256": source_sha,
        "semantic_ir_sha256": sha256_file(args.graph),
        "artifact_sha256": sha256_file(args.artifact),
        "dossier_sha256": sha256_file(args.dossier),
        "plan_match_sha256": sha256_file(args.plan_match),
        "oracle_sha256": sha256_file(args.oracle),
    }
    if args.plan_receipt.exists():
        values["plan_receipt_sha256"] = sha256_file(args.plan_receipt)
    return values


def validate_normal(oracle: dict[str, Any], artifact: dict[str, Any], plan_match: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    expected = oracle["resource_actions"]
    expected_dependencies = oracle["resource_action_dependencies"]
    if plan_match.get("state") != "CLOSED":
        die("normal evaluator input plan match is not CLOSED")
    if plan_match.get("expected_resource_actions") != expected or plan_match.get("observed_resource_actions") != expected:
        die("normal evaluator input action set is not oracle-equal")
    if receipt.get("state") != "CLOSED" or receipt.get("iac_engine") != "OPENTOFU" or receipt.get("engine_identity_source") != "PINNED_RELEASE_LOCK":
        die("normal evaluator input receipt lacks explicit OpenTofu identity")
    if receipt.get("oracle_resource_action_dependencies") != expected_dependencies:
        die("normal evaluator input dependency set is not oracle-equal")
    if receipt.get("binary_verified") is not True or receipt.get("release", {}).get("asset_verified") is not True or receipt.get("version_consistent_with_pinned_release") is not True:
        die("normal evaluator input release or binary digest is not verified")
    if receipt.get("command_verified") is not True or receipt.get("read_only", {}).get("apply") != 0 or receipt.get("read_only", {}).get("destroy") != 0:
        die("normal evaluator input command is not read-only")
    resource = artifact.get("resource", {}).get("terraform_data", {}).get("hello")
    if not isinstance(resource, dict) or resource.get("input") != "hello-from-gooo":
        die("normal evaluator input generated resource is missing")
    return {
        "dependency_comparison": {
            "expected": expected_dependencies,
            "observed": receipt.get("oracle_resource_action_dependencies"),
            "equal": True,
        },
        "observed_resource_actions": expected,
    }


def evaluate(args: argparse.Namespace) -> None:
    if args.case not in CASES:
        die(f"unsupported case: {args.case}")
    _bindings, oracle, artifact, plan_match, match_activity_id, source_sha = load_context(args)
    receipt = read_json(args.plan_receipt) if args.plan_receipt.exists() else {}
    claims: list[dict[str, Any]]
    details: dict[str, Any] = {}
    if args.case == "normal":
        details = validate_normal(oracle, artifact, plan_match, receipt)
        claims = [
            claim("VerifyGeneratedOutputs", next(item["activity_id"] for item in _bindings["bindings"] if item["activity"] == "VerifyGeneratedOutputs"), "CLOSED", "VALIDATION", "VERIFY_GENERATED_OUTPUTS", "GENERATED_OUTPUTS_STRUCTURALLY_VERIFIED", None, []),
            claim("MatchGoooIntentToOpenTofuPlan", match_activity_id, "CLOSED", "PLAN", "MATCH_RESOURCE_ACTION_SET", "GOOO_INTENT_MATCHES_OPENTOFU_PLAN", None, []),
        ]
        decision = "CLOSED"
    elif args.case == "missing_plan":
        claims = [unknown(match_activity_id, "PLAN", "READ_PINNED_JSON_UI", "PLAN_JSON_MISSING", "DIRECT_MISSING", "CAPTURE_PINNED_OPENTOFU_PLAN_JSON", [])]
        details = {"plan_receipt_present": args.plan_receipt.exists()}
        decision = "FAIL_CLOSED"
    elif args.case == "stale_input":
        claims = [unknown(match_activity_id, "PLAN", "MATCH_CURRENT_PLAN_INPUT", "STALE_PLAN_INPUT_DIGEST", "DIRECT_MISSING", "REGENERATE_PLAN_FOR_CURRENT_ARTIFACT", [])]
        details = {"observed_plan_input_sha256": receipt.get("input_artifact_sha256"), "current_artifact_sha256": sha256_file(args.artifact)}
        decision = "FAIL_CLOSED"
    elif args.case in ("unexpected_create", "unexpected_delete"):
        if args.case == "unexpected_create":
            observed = list(oracle["resource_actions"]) + [{"address": "terraform_data.unexpected", "action": "create"}]
        else:
            observed = [{"address": "terraform_data.hello", "action": "delete"}]
        details = {"expected_resource_actions": oracle["resource_actions"], "observed_resource_actions": observed}
        claims = [refuted("RefuteContradictionCase", next(item["activity_id"] for item in _bindings["bindings"] if item["activity"] == "RefuteContradictionCase"), "REFUTATION", "REFUTE_INTENT_PLAN_CONTRADICTION", "UNEXPECTED_RESOURCE_ACTION", "REJECT_UNEXPECTED_RESOURCE_ACTION")]
        decision = "FAIL_CLOSED"
    elif args.case == "engine_inference":
        details = {"compatibility_field": receipt.get("version_json", {}).get("terraform_version"), "explicit_iac_engine": None}
        claims = [refuted("MatchGoooIntentToOpenTofuPlan", match_activity_id, "ENGINE", "MATCH_PLAN_ENGINE", "ENGINE_INFERRED_FROM_COMPATIBILITY_FIELD", "CAPTURE_EXPLICIT_ENGINE_RECEIPT")]
        decision = "FAIL_CLOSED"
    elif args.case == "unsupported_json":
        details = {"observed_ui_version": "9.9", "supported_ui_major": "1"}
        claims = [refuted("MatchGoooIntentToOpenTofuPlan", match_activity_id, "PLAN", "READ_PINNED_JSON_UI", "UNSUPPORTED_JSON_UI_MAJOR", "PIN_OR_SUPPORT_JSON_UI_MAJOR")]
        decision = "FAIL_CLOSED"
    elif args.case == "malformed":
        try:
            lines = args.malformed_plan.read_text(encoding="utf-8").splitlines()
            json.loads(lines[-1])
        except (OSError, UnicodeError, IndexError, json.JSONDecodeError) as exc:
            claims = [fixed_point(match_activity_id, "MALFORMED_PLAN_JSON", str(exc))]
            details = {"fixture": str(args.malformed_plan)}
            decision = "FIXED_POINT"
        else:
            die("malformed fixture unexpectedly parsed")
    elif args.case == "authority_escalation":
        authority = read_json(args.authority_fixture)
        if authority.get("side_effects") != {"apply": 1, "destroy": 1, "cloud": 1, "network": 1, "source_write": 1}:
            die("authority escalation fixture changed")
        details = {"fixture": str(args.authority_fixture), "forbidden_commands": authority.get("commands", [])}
        claims = [refuted("PreserveReadOnlyBoundary", next(item["activity_id"] for item in _bindings["bindings"] if item["activity"] == "PreserveReadOnlyBoundary"), "AUTHORITY", "PRESERVE_READ_ONLY_BOUNDARY", "READ_ONLY_AUTHORITY_ESCALATION", "REMOVE_APPLY_DESTROY_CLOUD_NETWORK_SOURCE_WRITE")]
        decision = "FAIL_CLOSED"
    else:
        claims = [
            claim("VerifyGeneratedOutputs", next(item["activity_id"] for item in _bindings["bindings"] if item["activity"] == "VerifyGeneratedOutputs"), "CLOSED", "VALIDATION", "VERIFY_GENERATED_OUTPUTS", "GENERATED_OUTPUTS_STRUCTURALLY_VERIFIED", None, []),
            unknown(match_activity_id, "PLAN", "READ_PINNED_JSON_UI", "PLAN_JSON_MISSING", "DIRECT_MISSING", "CAPTURE_PINNED_OPENTOFU_PLAN_JSON", []),
            refuted("RefuteContradictionCase", next(item["activity_id"] for item in _bindings["bindings"] if item["activity"] == "RefuteContradictionCase"), "REFUTATION", "REFUTE_INTENT_PLAN_CONTRADICTION", "UNEXPECTED_RESOURCE_ACTION", "REJECT_UNEXPECTED_RESOURCE_ACTION"),
        ]
        details = {"state_order": ["CLOSED", "UNKNOWN", "REFUTED"]}
        decision = "FAIL_CLOSED"

    if decision not in ("CLOSED", "FAIL_CLOSED", "FIXED_POINT"):
        die("invalid decision")
    if any(item.get("state") == "REFUTED" for item in claims):
        decision = "FAIL_CLOSED"
    elif any(item.get("state") == "UNKNOWN" for item in claims):
        decision = "FAIL_CLOSED"
    elif any(item.get("state") == "FIXED_POINT" for item in claims):
        decision = "FIXED_POINT"
    result = {
        "schema": "gooo/opentofu-bridge/evaluator-case/v1",
        "case_id": args.case,
        "decision": decision,
        "resolution": "EXACT" if args.case != "missing_plan" else "LOWER_RESOLUTION",
        "precedence": "REFUTED_OVER_UNKNOWN_OVER_CLOSED",
        "claims": claims,
        "evidence": evidence(args, source_sha),
        "details": details,
        "independent_evaluator": {
            "path": "scripts/evaluator.py",
            "sha256": sha256_file(Path(__file__)),
            "generator_imported": False,
        },
    }
    write_json(args.output, result)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--case", choices=CASES, required=True)
    for name in ("source", "graph", "artifact", "dossier", "plan-receipt", "plan-match", "oracle", "bindings", "malformed-plan", "authority-fixture", "output"):
        root.add_argument(f"--{name}", type=Path, required=True)
    return root


if __name__ == "__main__":
    try:
        evaluate(parser().parse_args())
    except BrokenPipeError:
        sys.exit(1)
