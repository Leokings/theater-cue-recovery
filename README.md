# Theater Cue Recovery

Triages rehearsal-only cue incidents against a sealed dependency sheet and records a manager-selected recovery action.

This standalone repository contains one reusable GenLayer Intelligent Contract, direct tests, a five-validator GLSim flow, and an opt-in StudioNet smoke test. It has no frontend, token, payout, proxy, or repository secret.

## GenLayer-native decision

cue dependency sheet -> rehearsal report -> validator triage -> stage-manager action -> reporter acknowledgement.

## Evidence boundary

Cue descriptions and incident reports are public rehearsal text. Validators do not observe a stage, equipment, people, or live conditions.

## Limits

This is a fictional or rehearsal coordination aid, never emergency, equipment, workplace, or public-safety advice.

## Verify

Run GenVM lint and type-checking before tests, then direct tests, then the five-validator integration test. StudioNet evidence is written to deployments/studionet.json only after finalized successful execution and final-state readback.

