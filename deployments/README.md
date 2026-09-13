# Deployment evidence

`studionet.json` is a legacy v1 record retained for history. It does not prove the hardened v2 source and must not be used as the current submission artifact.

The current v2 submission target is a fresh StudioNet deployment. The release harness writes a resumable local checkpoint containing redacted finalized receipts and `LATEST_FINAL` readbacks. After that StudioNet checkpoint reaches `COMPLETE`, normalize it into an immutable submission manifest and verify it with:

```powershell
python scripts\verify_evidence.py --self-test
python scripts\verify_evidence.py --normalize deployments\studionet-v2-smoke.local.json --output deployments\studionet-v2-evidence.json
python scripts\verify_evidence.py deployments\studionet-v2-evidence.json --require-evidence-grade
```

Evidence manifests deliberately exclude private keys, mnemonics, credentials, and other sensitive receipt fields. They preserve a SHA-256 commitment to the original in-memory receipt, record every redacted path, and retain public transaction, execution, vote, equality-output, and state-readback evidence.

The manifest keeps address domains explicit: `contract.address` is the outer/routable StudioNet address used by transactions and explorer links, while `contract.genvm_address` is the address reported inside GenVM by matching `get_policy` and `get_rehearsal` reads. They are independently verified and are not assumed equal.

Never hand-edit a proof into `PASS`. A current proof must bind an exact Git commit, source blob/hash, generated ABI hash, constructor fixture, network/chain, finalized transactions, and final state.
