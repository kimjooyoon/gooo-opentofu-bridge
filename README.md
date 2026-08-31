# Gooo OpenTofu Bridge

This repository is a small, read-only vertical slice from a Gooo declaration
to a deterministic OpenTofu-compatible JSON configuration, a pinned
OpenTofu v1.12.6 validate/plan receipt, an independent evaluator result, and a
human dossier.
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
5. asks the pinned OpenTofu v1.12.6 binary to validate and plan the generated
   configuration with refresh disabled, no input, JSON UI, and detailed exit
   code;
6. verifies the release asset, checksums asset, extracted binary, explicit
   `iac_engine=OPENTOFU`, version JSON consistency, command, source/IR/config
   inputs, and toolchain digests;
7. compares the observed plan's resource/action set and intent dependency
   mapping with the independent oracle in
   [`fixtures/plan-oracle-v1.json`](fixtures/plan-oracle-v1.json);
8. runs independent evaluator cases for normal CLOSED, missing-plan and stale
   input UNKNOWN, unexpected create/delete, engine inference, unsupported JSON,
   and authority escalation REFUTED, plus malformed JSON FIXED_POINT and
   precedence; and
9. replays generation and records linked integer observations.

The workflow is the execution authority. Go 1.27 is installed for toolchain
identity only; local test, build, formatter, vet, and conformance executions
are all locked to zero. The generated artifact is structure-checked by the
bridge and validated by the official OpenTofu release. All generated files are
written under caller-owned temporary output. No apply, destroy, init, provider,
cloud, network, or repository source write is part of this slice.

## Semantic authority

The `.gooo` file is the only infrastructure intent input. The released Gooo
graph is authoritative for activity identity and dependency edges. Every
observed user-path step, output count, physical-line count, wall time, peak RSS
value, exact identity digest, and verification execution/reuse count is bound
to an activity ID from that graph in `bridge-observation.json`.

`REFUTED` has precedence over `UNKNOWN`, which has precedence over `CLOSED`.
UNKNOWN claims always retain `stage`, `step`, `reason`, `unknown_class`,
`next_operation`, and `blocked_by`. The evaluator is deliberately a separate
script and does not import the generator. Exact before/after evidence and
independent user evidence are not supplied, so improvement and utility remain
UNKNOWN. `cross_project_required_gates` is explicitly zero.

The evidence artifact records directories/files, Go/Gooo/OpenTofu physical
files and lines (excluding the root README), build/test/conformance wall time
in milliseconds and raw nanoseconds, peak RSS, executed/reused/skipped tests,
generated artifact files/bytes, all identity digests, and
`repository_writes=0`.
