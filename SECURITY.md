# Security

## Trust model

The stage manager controls reporter admission, the cue sheet, recovery selection, and administrative cancellation. Authorized reporters control their own incident submissions and acknowledgements. Wallet addresses are role selectors, not proof of real-world identity or authority.

GenLayer validators interpret only the public on-chain scope, sealed cue sheet, and incident report. Consensus reduces dependence on one model; it does not prove that a reported event occurred and does not eliminate correlated model error.

## Defended properties

- No native value is accepted.
- Public storage growth is bounded: 16 cues, 16 registered reporter addresses, 64 lifetime incidents, 12 open incidents, two open and eight lifetime incidents per reporter.
- Reporting requires manager authorization; cancellation releases open capacity without deleting history.
- Cue ordering is linear and successors are contract-derived, so `SKIP_TO_SUCCESSOR` always has one deterministic target.
- Every inconclusive or out-of-scope result fails closed with no invented cue.
- Unsafe controls, bidi controls, zero-width characters, oversized raw strings, and oversized UTF-8 inputs are rejected before prompt construction.
- Evidence is delimited as untrusted text before and after each prompt.
- Nondeterministic closures read only frozen local values.
- Leader and audit outputs reject missing, extra, mistyped, invented, or incoherent fields.
- References, submissions, and requests have sender/domain-bound replay digests.
- Public getters expose the complete evidence, workflow, derived fields, counters, and digest chain.

## Residual risks

- A manager may authorize a malicious reporter, choose an undesirable but permitted action, revoke access, or cancel a record.
- An authorized reporter may submit false or adversarial text. The contract classifies the text; it does not observe a rehearsal.
- Competent validators may still share the same semantic error or reject an ambiguous case.
- A final result can be delayed or fail to reach consensus. Integrations must handle pending, failed, appealed, and inconclusive outcomes.
- Public input is permanent. Do not submit secrets, personal data, private scripts, keys, copyrighted private material, or confidential incident details.
- The total lifetime bound is intentional. Long-running deployments should roll to a new rehearsal contract rather than weakening storage bounds.

## Prohibited use

This contract is a fictional/rehearsal continuity primitive. It is not emergency guidance, workplace or crowd-safety advice, equipment control, medical judgment, legal evidence, or a substitute for qualified stage personnel. It must never autonomously operate physical systems.

## Integration requirements

Consume only finalized decisions from the expected chain, address, policy version, configuration digest, incident ID, and request digest. Treat action text as a bounded coordination record, not executable calldata. Apply independent authorization and replay controls before any downstream effect.
