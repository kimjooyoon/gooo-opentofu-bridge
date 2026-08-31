# Gooo OpenTofu Bridge

This repository is a small, read-only vertical slice from a Gooo declaration
to a deterministic OpenTofu-compatible JSON configuration, a pinned
machine-readable plan receipt, and a human dossier.
It does not implement OpenTofu, install providers, contact a cloud, or apply
infrastructure.

The example declares one `terraform_data.hello` intent in
[`examples/intent/main.gooo`](examples/intent/main.gooo). GitHub Actions then:

1. acquires the immutable Gooo Core and OpenTofu release assets recorded in
   [`contracts/release-lock-v1.json`](contracts/release-lock-v1.json);
2. fetches the JSON configuration specification at the pinned OpenTofu commit
   and verifies its SHA-256;
3. dumps and binds the released Gooo semantic graph to the fixed denominator;
4. generates exactly `intent.tf.json` and `dossier.md` deterministically;
5. asks the pinned OpenTofu binary to validate and plan the generated
   configuration with refresh disabled;
6. compares the plan's resource/action set with the independent oracle in
   [`fixtures/plan-oracle-v1.json`](fixtures/plan-oracle-v1.json);
7. evaluates normal, stale/unknown, unsupported-JSON UNKNOWN, and ignored-drift
   or contradictory REFUTED cases; and
8. replays generation and records linked integer observations.

The workflow is the execution authority. No local test, build, or formatter is
required for this repository. The generated artifact is structure-checked by
the bridge and validated by the official OpenTofu release when that execution
is available. If the official validation cannot execute, the structural result
remains CLOSED while the OpenTofu execution result is explicitly UNKNOWN with
its next operation and blocker preserved.

## Semantic authority

The `.gooo` file is the only infrastructure intent input. The released Gooo
graph is authoritative for activity identity and dependency edges. Every
observed user-path step, output count, physical-line count, wall time, peak RSS
value, exact identity digest, and verification execution/reuse count is bound
to an activity ID from that graph in `bridge-observation.json`.

`REFUTED` has precedence over `UNKNOWN`. UNKNOWN claims always retain
`stage`, `step`, `reason`, `unknown_class`, `next_operation`, and `blocked_by`.
The read-only profile never runs `tofu apply`, `tofu test`, provider
acceptance tests, cloud access, or source writes. The plan's resource/action
set is matched by an independent static oracle; a stale plan, inferred engine,
unsupported JSON UI major, or ignored drift cannot silently close.
