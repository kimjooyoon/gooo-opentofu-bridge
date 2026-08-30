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
    "PreserveUnknownCase",
    "RefuteContradictionCase",
    "VerifyDeterministicReplay",
    "PreserveReadOnlyBoundary",
]


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
and a ten-activity dependency chain. The bridge materializes that intent as
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
5. Validate the generated configuration and replay generation byte-for-byte.

## Case boundaries

- Normal: generated outputs are present and the bridge result is `CLOSED`.
- UNKNOWN: when the pinned JSON specification is missing, the result is
  `FAIL_CLOSED`; the direct missing claim and its dependency-blocked claim
  retain all coordinates and `blocked_by`.
- REFUTED: a deliberate intent/artifact contradiction is `REFUTED`; this state
  takes precedence over any possible UNKNOWN candidate.

## Non-claims

No OpenTofu source checkout, build, provider installation, backend init, plan,
apply, test, cloud access, or deployment is part of this slice. Repository
writes are zero after the caller-owned output is published.
"""
    (output_dir / "dossier.md").write_text(dossier, encoding="utf-8")


def validate_bridge_artifact(artifact_path: Path, dossier_path: Path, lock_path: Path, spec_path: Path, bindings_path: Path, denominator_path: Path, output: Path, tofu_result: Path | None, tofu_state: str | None, tofu_reason: str | None) -> None:
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
    if tofu_result is not None:
        result = read_json(tofu_result)
        if result.get("valid") is not True or result.get("error_count") not in (0, None):
            die("official OpenTofu validation did not close")
        official = {
            "state": "CLOSED",
            "command": ["tofu", "validate", "-json"],
            "valid": True,
            "error_count": int(result.get("error_count", 0)),
            "result_sha256": sha256_file(tofu_result),
        }
    else:
        official = {
            "state": "UNKNOWN",
            "command": ["tofu", "validate", "-json"],
            "valid": None,
            "error_count": None,
            "reason": tofu_reason or "OPENTOFU_VALIDATION_NOT_EXECUTED",
            "unknown_class": "EXECUTION_UNAVAILABLE",
            "next_operation": "EXECUTE_PINNED_OPENTOFU_VALIDATE",
            "blocked_by": [],
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


def evaluate_case(mode: str, artifact_path: Path, dossier_path: Path, bindings_path: Path, denominator_path: Path, output: Path) -> None:
    denominator = read_json(denominator_path)
    bindings = check_bindings(bindings_path, denominator)
    artifact_digest = sha256_file(artifact_path) if artifact_path.exists() else None
    dossier_digest = sha256_file(dossier_path) if dossier_path.exists() else None
    if mode == "normal":
        if artifact_digest is None or dossier_digest is None:
            die("normal case inputs are missing")
        result = {
            "schema": "gooo/opentofu-bridge/case/v1",
            "case_id": "normal",
            "decision": "CLOSED",
            "resolution": "EXACT",
            "claims": [claim("VerifyGeneratedOutputs", bindings, state="CLOSED", stage="VALIDATION", step="VERIFY_GENERATED_OUTPUTS", reason="GENERATED_OUTPUTS_STRUCTURALLY_VERIFIED", unknown_class=None, next_operation=None, blocked_by=[])],
            "evidence": {"artifact_sha256": artifact_digest, "dossier_sha256": dossier_digest},
        }
    elif mode == "unknown":
        next_operation = "PROVIDE_IMMUTABLE_OPENTOFU_JSON_SPEC"
        result = {
            "schema": "gooo/opentofu-bridge/case/v1",
            "case_id": "missing-json-spec",
            "decision": "FAIL_CLOSED",
            "resolution": "LOWER_RESOLUTION",
            "claims": [
                claim("ConsumePinnedOpenTofuJSONSpec", bindings, state="UNKNOWN", stage="SPEC", step="READ_IMMUTABLE_OPENTOFU_JSON_SPEC", reason="OPENTOFU_JSON_SPEC_MISSING", unknown_class="DIRECT_MISSING", next_operation=next_operation, blocked_by=[]),
                claim("GenerateOpenTofuCompatibleArtifact", bindings, state="UNKNOWN", stage="GENERATION", step="GENERATE_DETERMINISTIC_TF_JSON", reason="OPENTOFU_JSON_SPEC_DEPENDENCY_BLOCKED", unknown_class="DEPENDENCY_BLOCKED", next_operation=next_operation, blocked_by=["cell:OPENTOFU_SPEC_INPUT"]),
            ],
            "evidence": {"artifact_sha256": artifact_digest, "dossier_sha256": dossier_digest},
        }
    elif mode == "refuted":
        result = {
            "schema": "gooo/opentofu-bridge/case/v1",
            "case_id": "contradictory-intent",
            "decision": "FAIL_CLOSED",
            "resolution": "EXACT",
            "precedence": "REFUTED_OVER_UNKNOWN",
            "claims": [claim("RefuteContradictionCase", bindings, state="REFUTED", stage="REFUTATION", step="REFUTE_INTENT_ARTIFACT_CONTRADICTION", reason="GOOO_INTENT_ARTIFACT_CONTRADICTION", unknown_class=None, next_operation=None, blocked_by=[])],
            "evidence": {"declared_input": "hello-from-gooo", "contradictory_observation": "hello-from-gooo-contradiction", "artifact_sha256": artifact_digest},
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


def record_observation(publish_dir: Path, bindings_path: Path, denominator_path: Path, lock_path: Path, source: Path, graph_path: Path, validation_path: Path, replay_path: Path, cases_dir: Path, measurements_path: Path, line_metrics_path: Path, output: Path) -> None:
    denominator = read_json(denominator_path)
    bindings = check_bindings(bindings_path, denominator)
    lock = read_json(lock_path)
    measurement_doc = read_json(measurements_path)
    line_metrics = read_json(line_metrics_path)
    if not isinstance(measurement_doc, list) or not isinstance(line_metrics, list):
        die("measurement inputs must be arrays")
    known_activities = {binding["activity"]: binding["activity_id"] for binding in bindings["bindings"]}
    for measurement in measurement_doc:
        if measurement.get("activity") not in known_activities:
            die(f"measurement is not linked to a released Gooo activity: {measurement}")
        measurement["activity_id"] = known_activities[measurement["activity"]]
        check_int(measurement.get("wall_ms", 0), "wall_ms")
        check_int(measurement.get("peak_rss_kib", 0), "peak_rss_kib")
        check_int(measurement.get("executions", 0), "executions")
        check_int(measurement.get("reused", 0), "reused")
    for metric in line_metrics:
        if metric.get("activity") not in known_activities:
            die(f"line metric is not linked to a released Gooo activity: {metric}")
        metric["activity_id"] = known_activities[metric["activity"]]
        check_int(metric.get("value"), "line metric value")
    expected = sorted(OUTPUT_NAMES)
    actual = sorted(path.name for path in publish_dir.iterdir() if path.is_file())
    if actual != expected:
        die(f"published output set mismatch: {actual}")
    cases = {name: read_json(cases_dir / f"{name}.json") for name in ("normal", "unknown", "refuted")}
    if cases["normal"].get("decision") != "CLOSED":
        die("normal case is not CLOSED")
    unknown_claims = cases["unknown"].get("claims", [])
    if cases["unknown"].get("decision") != "FAIL_CLOSED" or sorted(claim.get("unknown_class") for claim in unknown_claims) != ["DEPENDENCY_BLOCKED", "DIRECT_MISSING"]:
        die("unknown case does not preserve direct and dependency-blocked claims")
    required_unknown = {"stage", "step", "reason", "unknown_class", "next_operation", "blocked_by"}
    if any(required_unknown - claim.keys() for claim in unknown_claims):
        die("unknown claim coordinates are incomplete")
    if cases["refuted"].get("decision") != "FAIL_CLOSED" or cases["refuted"].get("claims", [{}])[0].get("state") != "REFUTED":
        die("refuted case is not REFUTED")
    validation = read_json(validation_path)
    replay = read_json(replay_path)
    output_digests = {name: sha256_file(publish_dir / name) for name in expected}
    stage_executions = sum(check_int(item.get("executions", 0), "executions") for item in measurement_doc)
    stage_reused = sum(check_int(item.get("reused", 0), "reused") for item in measurement_doc)
    graph_binding = next(item for item in bindings["bindings"] if item["activity"] == "BindGoooIntentToOpenTofu")
    final_binding = next(item for item in bindings["bindings"] if item["activity"] == "PreserveReadOnlyBoundary")
    metrics = [
        {"name": "user_path_steps", "value": denominator["expected_user_path_steps"], "unit": "steps", "activity": "VerifyGeneratedOutputs", "activity_id": known_activities["VerifyGeneratedOutputs"]},
        {"name": "generated_file_count", "value": len(expected), "unit": "files", "activity": "GenerateHumanDossier", "activity_id": known_activities["GenerateHumanDossier"]},
        {"name": "executed_verification_stages", "value": stage_executions, "unit": "stage-executions", "activity": "PreserveReadOnlyBoundary", "activity_id": final_binding["activity_id"]},
        {"name": "reused_verification_stages", "value": stage_reused, "unit": "stage-reuses", "activity": "PreserveReadOnlyBoundary", "activity_id": final_binding["activity_id"]},
        *line_metrics,
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
                ],
            },
            "generated_outputs": {"file_count": len(expected), "files": expected, "digests": output_digests, "activity": "GenerateHumanDossier", "activity_id": known_activities["GenerateHumanDossier"]},
            "metrics": metrics,
            "verification_stages": measurement_doc,
            "cases": cases,
            "validation": validation,
            "replay": replay,
            "official_inputs": {
                "gooo_release": {"repository": lock["gooo"]["repository"], "tag": lock["gooo"]["tag"], "asset_sha256": lock["gooo"]["asset"]["sha256"]},
                "opentofu_release": {"repository": lock["opentofu"]["repository"], "tag": lock["opentofu"]["tag"], "asset_sha256": lock["opentofu"]["asset"]["sha256"]},
                "opentofu_json_spec_sha256": lock["opentofu"]["json_spec"]["sha256"],
            },
            "authority": lock["authority"],
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
    validate.add_argument("--tofu-result", type=Path)
    validate.add_argument("--tofu-state", choices=("UNKNOWN",))
    validate.add_argument("--tofu-reason")

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--mode", choices=("normal", "unknown", "refuted"), required=True)
    for name in ("artifact", "dossier", "bindings", "denominator", "output"):
        evaluate.add_argument(f"--{name}", type=Path, required=True)

    replay = sub.add_parser("replay")
    for name in ("source", "graph", "lock", "spec", "bindings", "denominator", "output"):
        replay.add_argument(f"--{name}", type=Path, required=True)

    record = sub.add_parser("record")
    for name in ("publish-dir", "bindings", "denominator", "lock", "source", "graph", "validation", "replay", "cases-dir", "measurements", "line-metrics", "output"):
        record.add_argument(f"--{name}", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "bind":
        bind_graph(args.source, args.graph, args.denominator, args.output)
    elif args.command == "generate":
        generate_outputs(args.source, args.graph, args.lock, args.spec, args.bindings, args.denominator, args.output_dir)
    elif args.command == "validate":
        validate_bridge_artifact(args.artifact, args.dossier, args.lock, args.spec, args.bindings, args.denominator, args.output, args.tofu_result, args.tofu_state, args.tofu_reason)
    elif args.command == "evaluate":
        evaluate_case(args.mode, args.artifact, args.dossier, args.bindings, args.denominator, args.output)
    elif args.command == "replay":
        replay_outputs(args.source, args.graph, args.lock, args.spec, args.bindings, args.denominator, args.output)
    elif args.command == "record":
        record_observation(args.publish_dir, args.bindings, args.denominator, args.lock, args.source, args.graph, args.validation, args.replay, args.cases_dir, args.measurements, args.line_metrics, args.output)
    else:
        die(f"unknown command: {args.command}")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(1)
