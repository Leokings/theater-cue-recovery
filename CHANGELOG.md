# Changelog

## 2.0.0 — 2026-09-13

- Removed every storage read from nondeterministic execution.
- Replaced exact validator re-solving with a bounded independent candidate audit.
- Added `AMBIGUOUS`, `INSUFFICIENT_EVIDENCE`, and `OUT_OF_SCOPE` fail-closed results.
- Added manager-authorized reporters, per-reporter quotas, and auditable capacity-releasing cancellation.
- Enforced one linear cue sequence and deterministic recovery targets.
- Added canonical identifiers, UTF-8/control-character limits, native-value rejection, replay protection, full indexed readbacks, and domain-separated digest lineage.
- Expanded direct and five-validator integration coverage and added commit-bound deployment evidence tooling.

## 1.0.0 — 2026-08-25

- Initial StudioNet proof of concept.
- Historical only: the v1 semantic receipt finalized but emitted a GenVM warning because validation read contract storage inside nondeterministic execution.
