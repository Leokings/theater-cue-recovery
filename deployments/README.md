# Deployment evidence

`studionet.json` is a legacy v1 record retained for history. It does not prove the hardened v2 source and must not be used as the current submission artifact.

The v2 release harness writes a resumable local checkpoint containing redacted finalized receipts and `LATEST_FINAL` readbacks. A completed checkpoint is normalized into an immutable submission manifest and checked against `schema.json` with:

```powershell
python scripts\verify_evidence.py --self-test
python scripts\verify_evidence.py deployments\<manifest>.json --require-evidence-grade
```

Evidence manifests deliberately exclude private keys, mnemonics, credentials, and other sensitive receipt fields. They preserve a SHA-256 commitment to the original in-memory receipt, record every redacted path, and retain public transaction, execution, vote, equality-output, and state-readback evidence.

Never hand-edit a proof into `PASS`. A current proof must bind an exact Git commit, source blob/hash, generated ABI hash, constructor fixture, network/chain, finalized transactions, and final state.
