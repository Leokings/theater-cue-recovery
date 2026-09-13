# Audit

Audit date: 2026-09-13
Release candidate: `2.0.0` / `THEATER_CUE_RECOVERY_V2`

## Verdict

The hardened v2 contract passes the local release audit. Its current source was also deployed through the canonical StudioNet RPC. The deployment finalized with GenVM `SUCCESS` and Accepted consensus (3 agree, 2 idle after quorum), but StudioNet did not expose the new address through `gen_getContractSchema` or `gen_call` during the capture window. The deployment-only proof therefore does not claim a current-source lifecycle smoke or finalized state readback. The historical v1 StudioNet address is not evidence for v2.

This is a reusable rehearsal-continuity primitive, not production theater-safety software. The audit covers contract behavior and the bundled evidence fixture, not the truth of arbitrary incident reports.

## Reproduced checks

| Check | Result |
|---|---|
| GenVM lint and semantic validation | PASS — 3/3 checks |
| Public schema | PASS — 16 methods, 8 write, 8 view, 2 constructor parameters |
| GenVM-aware Pyright | PASS — 0 diagnostics |
| Generated `abi.json` versus source | PASS |
| Offline direct/tooling suite | PASS — 144/144 |
| Five-validator GLSim | PASS — 4/4 |
| Evidence-verifier self-test | PASS — 8/8 |
| Test collection | PASS — 149 total, including one opt-in external-network smoke |
| Python dependency consistency | PASS |
| Current-source StudioNet deployment | PASS — FINALIZED / SUCCESS / Accepted |
| Current-source StudioNet lifecycle readback | NOT RECORDED — RPC returned contract not found after deployment |

The tests cover native-value rejection, text and UTF-8 bounds, unsafe Unicode, role transitions, reporter quotas, reference/report/request replay protection, one linear cue sequence, all four semantic statuses, coherent anchor and impact rules, the complete allowed/rejected action matrix, deterministic targets, cancellation and acknowledgement, malformed leader and audit outputs, prompt injection, digest lineage, indexed readbacks, failed-write atomicity, ABI drift, and evidence redaction/validation.

## Findings resolved in v2

1. V1 read `self.cue_ids` inside nondeterministic execution. Its live StudioNet receipt emitted `Detected pickling storage class. Reading storage in nondet mode is not supported`. V2 snapshots every storage value before consensus, and source-structure tests enforce the boundary.
2. V1 allowed any address to exhaust a permanent 12-incident quota. V2 requires manager-authorized reporters, limits each reporter, separates open and lifetime bounds, and supports append-only manager cancellation that releases open capacity.
3. V1 forced every report into a cue and impact. V2 adds `AMBIGUOUS`, `INSUFFICIENT_EVIDENCE`, and `OUT_OF_SCOPE`; these cannot carry an invented anchor.
4. V1 required validators to reproduce a subjective two-field answer exactly. V2 has a three-field leader candidate and an independent one-boolean semantic audit.
5. V1 permitted branches but did not store a successor target. V2 derives one linear sequence and the exact action target.
6. V1 canonicalized identifiers inconsistently and exposed incomplete evidence. V2 uses one ASCII identifier policy and complete indexed readbacks with digest provenance.
7. Integration tests found and fixed ABI string-address handling, Address serialization in views, eager secret interpolation, and JSON Unicode expansion that made valid byte-bounded inputs exceed the prompt ceiling.

## Evidence integrity

The release harness binds source, generated ABI, harness, constructor, Git commit, network and chain. It persists only recursively redacted receipts, commits to each original in-memory receipt by SHA-256, records redaction paths, checkpoints each finalized operation, and verifies all public views through `LATEST_FINAL`.

The normalized evidence manifest is checked by the standard-library verifier and `deployments/schema.json`. A proof is not considered evidence-grade unless the verifier passes with `--require-evidence-grade`.

## Known limitations

- Semantic accuracy is bounded by the supplied public text and validator models.
- Consensus may be delayed, fail, or be appealed; consumers must wait for finality.
- GLSim 0.29.2 does not reliably restore leader-proposed state during an all-disagree mock rotation. That simulator-specific case is tested in direct mode; GLSim covers malformed-leader rollback and unauthorized-write rollback instead.
- The bundled semantic smoke is one unambiguous fixture, not a claim of universal accuracy.
- The contract records an action but never executes a physical or financial consequence.
- V1 StudioNet records remain historical and include the unsupported-storage warning; they must not be represented as current-source proof.
- The current-source StudioNet deployment receipt is valid, but the address was unavailable to the StudioNet schema/read RPC during capture; `deployments/studionet-v2-deployment.json` limits its claim accordingly.

## Commands

```powershell
genvm-lint check contracts\theater_cue_recovery.py --json
genvm-lint typecheck contracts\theater_cue_recovery.py
pytest tests\direct -q
pytest tests\integration\test_glsim_consensus.py -q
python scripts\verify_evidence.py --self-test
```
