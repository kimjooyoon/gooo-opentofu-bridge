# Gooo OpenTofu Bridge

This repository is a small, read-only vertical slice from a Gooo declaration
to a deterministic OpenTofu-compatible JSON configuration and a human dossier.
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
5. asks the pinned OpenTofu binary to validate the generated configuration;
6. evaluates one normal, one missing-input UNKNOWN, and one contradictory
   REFUTED case; and
7. replays generation and records linked integer observations.

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
value, and verification execution/reuse count is bound to an activity ID from
that graph in `bridge-observation.json`.

`REFUTED` has precedence over `UNKNOWN`. UNKNOWN claims always retain
`stage`, `step`, `reason`, `unknown_class`, `next_operation`, and `blocked_by`.

