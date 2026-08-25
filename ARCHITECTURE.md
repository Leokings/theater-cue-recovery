# Architecture

## Flow

cue dependency sheet -> rehearsal report -> validator triage -> stage-manager action -> reporter acknowledgement.

## Responsibility boundary

Frontend or backend: wallet UX, indexing, private drafts, and non-authoritative previews.

GenLayer contract: frozen public evidence, an independently validated semantic decision, deterministic state transitions, and the explicit human-controlled terminal action described in the contract.

External systems: none are queried by this version.

## Originality boundary

This repository uses its own domain state machine, AI role, stored artifacts, and terminal effect. Its originality is measured against the workspace corpus before handoff.

