# Architecture

## The one boundary that matters

```
                    CONSENSUS-CRITICAL                DETERMINISTIC
                    (validators must agree)           (pure integer code)
                 ┌────────────────────────┐      ┌──────────────────────┐
 Agreement ──────┤                        │      │                      │
 Requirements ───┤   GenLayer panel       │      │  weight arithmetic   │
 Evidence refs ──┤   each node FETCHES    ├─────►│  payout / refund     │──► transfer
 Deadlines ──────┤   and VERIFIES the     │      │  escrow zeroing      │
                 │   artifacts, judges    │      │                      │
                 │   verified ones only,  │      │                      │
                 │   returns FACTS        │      │                      │
                 └────────────────────────┘      └──────────────────────┘
                   per-requirement PASS/FAIL        provider_payout
                   deadline_met                     client_refund
                   evidence_examined                penalty
                   per-record verified
```

The panel never sees a monetary figure and never returns one. The
contract never asks a model what something is worth. Everything to the
left of the arrow is a question about the world; everything to the right
is arithmetic over committed numbers.

## Layers

```
Agreement / Terms
       ↓          create_agreement, update_terms (DRAFT only)
Requirements
       ↓          weighted, unique ids, must sum to 100
Terms Commitment
       ↓          sha256 over the frozen term set; locked at funding
Evidence Commitments
       ↓          submit_evidence, supersede_evidence, challenge_evidence
                  (a reference + an identity; nothing fetched yet)
GenLayer Adjudication
       ↓          request_adjudication → gl.vm.run_nondet_unsafe
Evidence Acquisition + Verification   (leader AND every validator)
       ↓          gl.nondet.web.get → _verify_artifact → evidence rule (see EVIDENCE.md)
Validator Equivalence
       ↓          decision fingerprint comparison (see CONSENSUS.md)
Finality / Appeal
       ↓          ACCEPTED → (appeal window) → FINALIZED
Deterministic Settlement
                  settle() → weight × escrow → _send_gen
```

## State machine

```
DRAFT ──fund_agreement──► FUNDED ──accept_agreement──► ACTIVE
  │                         │                            │
  │                         │                            ├──submit_deliverable──► SUBMITTED
  │                         │                            │                            │
  └──cancel──► CANCELLED    └──cancel──► CANCELLED        └──expire──► EXPIRED         │
                                                                        │              │
                                                       request_adjudication ◄──────────┘
                                                                        │
                                                    ┌───────────────────┴──────────────┐
                                                    ▼                                  ▼
                                              UNDETERMINED                        ACCEPTED
                                                    │                                  │
                            (more evidence)         │                    ┌─────────────┴──────────┐
                            request_adjudication ◄──┤                    │                        │
                                                    │                 appeal                  finalize
                            recover_escrow ─────────┘                    │                        │
                                    │                                    ▼                        ▼
                                    ▼                                APPEALED               FINALIZED
                                REFUNDED                                 │                        │
                                                       request_adjudication                    settle
                                                                         │                        │
                                                                    (back to ACCEPTED)            ▼
                                                                                              SETTLED
```

Every transition validates caller, current state, timing, required
fields, escrow and evidence before it writes anything.

## Where value can leave

Exactly three functions emit GEN, and all three route through the single
helper `_send_gen`:

| Function | Condition | Recipient |
|---|---|---|
| `settle` | status FINALIZED, verdict not UNDETERMINED | provider and/or client, per weights |
| `cancel_agreement` | status DRAFT or FUNDED | client (full refund) |
| `recover_escrow` | status EXPIRED / UNDETERMINED / APPEALED, past resolution deadline | client (full refund) |

Every one of them follows the same order:

```
read ledger → validate → compute → zero ledger → persist state → emit transfer
```

State is committed before any value moves, so a second call sees a
terminal status and is refused by the state machine before it can reach
a transfer.

## Storage shape

GenVM will not let contract code instantiate a `DynArray` or a nested
dataclass, so every list is stored as canonical JSON in a `str` field
and parsed at the view boundary. This is deliberate and load-bearing:

- `Agreement.requirements_json` — the frozen requirement set
- `Verdict.requirement_results_json` — per-requirement statuses
- `Verdict.evidence_examined_json` — cited evidence ids
- `Verdict.evidence_verification_json` — per-record verification rows
  (status and identities; never artifact content)

Evidence is the one collection stored as a real `DynArray`, because
records must be mutable in place (status changes). It lives in exactly
one place — `evidence_by_agreement[aid]` — with two `TreeMap` indexes
(`evidence_owner`, `evidence_index`) pointing into it. Storing the
record itself twice would let the copies drift, and a status change
written to one would be invisible through the other.

## Reusability

Nothing in the contract knows about datasets, APIs or any industry. A
consumer supplies the requirements, weights, deadlines and rules; the
contract hashes them and adjudicates against that frozen version. See
README's three reference examples — agent work, API SLA, data delivery —
all of which use the same unmodified primitive.
