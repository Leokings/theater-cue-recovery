# Architecture

## Responsibility boundary

The contract stores public rehearsal text, performs one bounded semantic classification through GenLayer consensus, derives allowed recovery records, and preserves an append-only audit trail.

A frontend or integrating contract remains responsible for wallet UX, indexing, notification, private drafting, and deciding whether to rely on a finalized record. No external system is queried or controlled.

## Hybrid execution

Deterministic code handles:

- manager and reporter authorization;
- input canonicalization, UTF-8 budgets, quotas, phases, and replay protection;
- linear cue predecessor/successor links;
- result-schema validation;
- allowed actions and target cues;
- counters and digest lineage; and
- terminal acknowledgement or cancellation.

Consensus handles only whether one immutable report is `MATCHED`, `AMBIGUOUS`, `INSUFFICIENT_EVIDENCE`, or `OUT_OF_SCOPE`, and—only for a match—which supplied cue index and impact class the evidence supports.

## Consensus boundary

Before `run_nondet_unsafe`, the contract copies the scope, sealed cue-sheet JSON, incident report, request digest, and cue count into plain local Python values. Neither closure reads `self`, `DynArray`, `TreeMap`, `gl.message`, or any other contract storage.

The leader produces exactly `status`, `anchor_index`, and `impact`. A validator first revalidates that structure, then independently asks whether the candidate is substantively supported. The audit output is exactly one boolean. Only a consensus-accepted candidate is written to incident state.

## State and provenance

The cue sheet is mutable only in `CUE_SHEET` mode and becomes immutable in `REHEARSAL` mode. Incidents follow:

```text
REPORTED -> TRIAGED -> ACTION_SELECTED -> ACKNOWLEDGED
     |          |              |
     +----------+--------------+-> CANCELLED (manager)
```

Each incident snapshots reporter-policy version and digest at submission. Length-framed Keccak digests bind the deployment domain, reporter, client reference, report, request, semantic decision, manager action, and terminal event. Indexed views expose every stored record needed to reconstruct the evidence chain.

## Deliberate exclusions

The contract has no web access, image/audio input, private evidence, oracle feed, token, transfer, arbitrary callback, proxy, upgrade path, equipment integration, or autonomous real-world action.
