# Theater Cue Recovery

Theater Cue Recovery is a reusable GenLayer Intelligent Contract for recording how a fictional or rehearsal-only cue sequence should resume after a reported continuity incident.

An authorized participant submits bounded public text. GenLayer consensus classifies it into one closed semantic result; deterministic contract code derives the permitted recovery actions and target cue. A stage manager chooses an allowed action, and the original reporter acknowledges it or the manager closes the record.

This contract never controls stage systems, people, equipment, payments, or emergencies.

## Why GenLayer

A conventional contract can enforce roles, limits, replay protection, and state transitions. It cannot reliably decide whether rehearsal prose clearly refers to one cue or whether the incident is local, affects later cues, or requires a full reset.

The consensus-critical output is deliberately small:

```json
{"status":"MATCHED","anchor_index":1,"impact":"LOCAL"}
```

The leader may return only:

- `MATCHED` with an existing cue index and `LOCAL`, `CHAIN`, or `RESET`;
- `AMBIGUOUS`;
- `INSUFFICIENT_EVIDENCE`; or
- `OUT_OF_SCOPE`.

Every non-match must use `anchor_index: -1` and `impact: "NONE"`. Validators independently audit the candidate and return only `{"accept":true}` or `{"accept":false}`. IDs, cue links, action targets, summaries, counters, and digests are derived deterministically.

## Lifecycle

```text
CUE_SHEET
  manager registers reporters and an ordered linear cue sheet
  manager seals the sheet
        |
        v
REHEARSAL
  authorized reporter -> REPORTED
  reporter or manager  -> TRIAGED (GenLayer consensus)
  manager              -> ACTION_SELECTED
  reporter             -> ACKNOWLEDGED

  manager may instead close any open record -> CANCELLED
```

For matched results, the contract permits only:

| Impact | Allowed action | Derived target |
|---|---|---|
| `LOCAL` | `REPEAT_CUE` | anchor cue |
| `CHAIN` | `SKIP_TO_SUCCESSOR` or `RESET_SEQUENCE` | successor or first cue |
| `RESET` | `RESET_SEQUENCE` | first cue |

`AMBIGUOUS` and `INSUFFICIENT_EVIDENCE` permit `REQUEST_CLARIFICATION` or `CLOSE_NO_ACTION`; `OUT_OF_SCOPE` permits only `CLOSE_NO_ACTION`.

## Public interface

```python
TheaterCueRecovery(rehearsal_id, rehearsal_scope)

set_reporter(reporter, authorized)
register_cue(cue_id, description)
seal_cue_sheet()
report_incident(client_reference, public_rehearsal_report) -> record_id
triage_incident(record_id)
choose_recovery(record_id, action)
acknowledge_recovery(record_id)
cancel_incident(record_id, reason)

get_policy()
get_rehearsal()
get_cue(cue_id)
get_cue_at(index)
get_reporter(reporter)
get_reporter_at(index)
get_incident(record_id)
get_incident_at(index)
```

The complete generated interface is in [abi.json](abi.json).

## Safety and reuse properties

- Content-addressed production GenVM runner and MIT license.
- Native value rejected on construction and every write.
- Uppercase ASCII identifiers; bounded characters and UTF-8 bytes; unsafe control, bidi, and zero-width characters rejected.
- A sealed, linear cue sequence with contract-derived predecessor and successor links.
- Manager-authorized reporters, per-reporter open/lifetime quotas, a global open limit, and manager cancellation to release capacity.
- Sender-scoped reference and report replay protection.
- Storage-free nondeterministic callbacks: all storage is copied to plain local values before consensus.
- Closed leader and validator schemas, explicit inconclusive outcomes, and prompt-injection boundaries before and after evidence.
- Length-framed, domain-separated provenance for reporter policy, cue sheet, configuration, requests, decisions, actions, and terminal records.
- Complete indexed readbacks for policies, cues, reporters, and incidents.

Read [SECURITY.md](SECURITY.md), [SOURCE_POLICY.md](SOURCE_POLICY.md), and [ARCHITECTURE.md](ARCHITECTURE.md) before integrating.

## Verify

```powershell
pip install -r requirements.txt

genvm-lint check contracts\theater_cue_recovery.py --json
genvm-lint typecheck contracts\theater_cue_recovery.py
genvm-lint schema contracts\theater_cue_recovery.py --json
pytest tests\direct -q

# In another terminal:
python tests\run_glsim.py --port 4000 --validators 5 --no-browser
pytest tests\integration\test_glsim_consensus.py -q

python scripts\verify_evidence.py --self-test
```

The opt-in release harness in `deploy/001_deploy_and_smoke.py` binds a deployment to an exact commit and source/ABI hashes, checkpoints every finalized transaction, reads every public view at `LATEST_FINAL`, and emits a redacted proof record. It requires distinct funded manager and reporter accounts; see [.env.example](.env.example).

## Evidence status

The old `deployments/studionet.json` record belongs to v1 and is retained only as historical evidence. It must not be presented as proof of this hardened v2 source. The current submission proof and address are recorded separately after a finalized v2 deployment.

## Reuse

Copy `contracts/theater_cue_recovery.py`, keep the bounded schemas and safety properties, choose a rehearsal ID and public scope, register the permitted reporters and ordered cues, then deploy. Applications should consume only finalized state.

## License

[MIT](LICENSE)
