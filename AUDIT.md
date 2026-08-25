# Audit

Status: PASS — final review and submission-blocker audit completed on 08/25/2026.

Verification:

- GenVM lint and semantic validation: PASS
- Pyright typecheck: PASS, zero errors
- Direct-mode tests: PASS, 3/3
- Five-validator GLSim: PASS, 1/1
- StudioNet finalized execution and LATEST_FINAL readback: PASS
- ABI-to-source schema comparison: PASS
- Workspace originality: PASS; 162 contracts scanned, new-corpus maximum 0.4276
- Runner pin, prompt-injection boundary, source policy, state/role/bounds, forbidden-operation, repository-shape, and secret scans: PASS

StudioNet:

- Batch: 2
- Public batch wallet: 0x9A4eE2aFeD53C517Ef5cd722ba94956AFE2A7b3d
- Contract address: 0xB8A34029eddB732200500Ec4E55AD3C140BE345f
- Deployment transaction: 0x695e2e7ed2f597909b6c7fd71669a006897f06ca8152f2370579e2368a4fee3c
- Intelligent transaction: 0x1a526e54db0c492db9c509ce96438b10e3e0f864c60c4a1c26198a1a90fcc94c

Reviewed scope: contracts/theater_cue_recovery.py, repository tests, source policy, security boundary, and deployments/studionet.json.
