# Portal submission

Paste-ready portal fields for the current v2 release. The evidence is deliberately limited to what was actually observed on StudioNet.

## Contribution Date

```text
09/13/2026
```

## Title

```text
Theater Cue Recovery — Reusable Intelligent Contract
```

## Notes / Description

```text
Built and deployed an MIT-licensed Theater Cue Recovery contract, a reusable GenLayer Intelligent Contract for rehearsal cue continuity. A manager seals a linear cue sheet; a reporter submits a missed-cue report; AI consensus returns MATCHED, AMBIGUOUS, INSUFFICIENT_EVIDENCE, or OUT_OF_SCOPE with a cue anchor and impact. Deterministic code derives permitted recovery actions and the target, then records selection and acknowledgement.

V2 limits the leader to three fields and validators to one boolean, snapshots storage before nondeterminism, rejects prompt-injection controls, enforces roles and quotas, prevents replay, and preserves digest lineage. It passes GenVM lint and typecheck, 144 direct tests, 4 five-validator GLSim tests, and 8 evidence self-tests. The current-source StudioNet deployment finalized with GenVM SUCCESS and Accepted consensus (3 agree, 2 idle after quorum). It makes no live-safety, equipment-control, financial, or current V2 lifecycle-readback claim.
```

## Evidence & Supporting

1. **GitHub Repository**
   `https://github.com/Leokings/theater-cue-recovery`

2. **GenLayer StudioNet Explorer Contract**
   `https://explorer-studio.genlayer.com/address/0x62122cb6486bc664084dec8c93ec6a61f1c51016`

3. **GitHub File — exact deployed contract source**
   `https://github.com/Leokings/theater-cue-recovery/blob/1b3203413c6c6f59a45a4388ed8f98d3aca4b786/contracts/theater_cue_recovery.py`

4. **GitHub File — StudioNet deployment evidence**
   `https://github.com/Leokings/theater-cue-recovery/blob/main/deployments/studionet-v2-deployment.json`

Category: **Intelligent Contracts**

Before submitting, make the repository public or grant the reviewers access. It is currently private.
