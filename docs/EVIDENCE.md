# Evidence

## Four things that are not the same

```
CLAIM                    "I completed the work."
                         A party's assertion. Proves nothing on its own.
                         In this contract: submit_deliverable().

EVIDENCE                 A dataset, a commit, an API response, a receipt.
                         An artefact someone else could look at.

EVIDENCE COMMITMENT      The on-chain record: id, requirement binding,
                         type, source reference, CONTENT HASH, submitter,
                         tick, version, status. What the panel reads.

AUTHORITATIVE SOURCE     Evidence a third party can independently
                         re-derive without trusting either party.
```

The distinction is the point of the primitive. A protocol that settles
on claims is a protocol that pays whoever asserts most confidently.

## The record

```
evidence_id        SLA-000001-E0003   deterministic, sequential per agreement
agreement_id       SLA-000001         binding — checked on every access
requirement_id     R2                 must exist in the committed set
submitter          0x…                the actual transaction caller
evidence_type      API_RESULT         from a closed vocabulary
source_reference   https://…          where it can be found
content_hash       sha256:…           THE COMMITMENT — written once, never mutated
description        free text          context for the panel
submitted_tick     41                 protocol clock at submission
version            1                  increments on supersede
status             ACTIVE             ACTIVE | SUPERSEDED | CHALLENGED
```

## Supported types

```
URL              API_RESULT       GITHUB_COMMIT    DOCUMENT
JSON             CSV              DATASET          BLOCKCHAIN_TX
SIGNED_MESSAGE   SERVICE_LOG      AGENT_OUTPUT     OTHER
```

An unrecognised type is refused at submission — the vocabulary is
closed so the panel prompt can reason about it reliably.

## Authoritative vs self-reported

```python
AUTHORITATIVE_TYPES = {
    "GITHUB_COMMIT", "BLOCKCHAIN_TX", "API_RESULT",
    "SIGNED_MESSAGE", "DATASET", "URL",
}
```

Every record surfaces an `authoritative: true|false` flag, derived from
its type, both in `get_evidence()` and in the snapshot handed to the
panel. The adjudication prompt says, in rule 3:

> Weigh EVIDENCE, not assertions. A party stating 'I completed the work'
> is a claim and proves nothing on its own. Records marked
> `"authoritative": true` … are independently checkable and carry more
> weight than prose.

The list is what it is because each member can be re-derived by a third
party: a commit hash resolves to bytes, a chain transaction to a
receipt, an API result to a re-request, a signed message to a
signature check, a dataset and a URL to a fetch-and-hash. `DOCUMENT`,
`SERVICE_LOG`, `AGENT_OUTPUT` and `OTHER` are deliberately excluded —
they are usually one party's own file.

## Immutability

**No method in this contract mutates a content hash.** There is no
`update_evidence`, no `edit_evidence`, no `set_evidence_hash`, no
`delete_evidence`. `test_C_committed_evidence_cannot_be_edited` asserts
their absence from the deployed schema.

Replacement is an explicit, additive operation:

```
supersede_evidence(agreement_id, evidence_id, new_source, new_hash, desc)

  old record:  status ACTIVE → SUPERSEDED    hash UNCHANGED, still readable
  new record:  fresh evidence_id, version = old.version + 1, status ACTIVE
```

Both records stay in the log forever. A reader can reconstruct exactly
what was committed, when, by whom, and what replaced it. Only the
original submitter may supersede, and a record can be superseded once.

Contesting is likewise additive: `challenge_evidence` sets the status to
`CHALLENGED` and appends a note to the description. The hash is
untouched, and the panel is told to say so in its reasoning rather than
silently ignore the record.

## Agreement binding

Every evidence access goes through `_resolve_evidence(evidence_id,
expected_agreement_id)`, which refuses a record belonging to a different
agreement **even when the caller is a legitimate party to both**. This
closes cross-agreement substitution in three places:

1. `supersede_evidence` — cannot replace a foreign record
2. `challenge_evidence` — cannot flag a foreign record
3. `request_adjudication` — after consensus, every cited evidence id is
   checked against this agreement's set; a foreign citation rolls the
   round back with escrow untouched

Point 3 matters most: **consensus agreeing on a reference does not make
the reference legitimate.** A unanimous panel citing another
agreement's evidence is still refused.

## Single source of truth

Evidence lives in exactly one place — the per-agreement `DynArray`. Two
`TreeMap`s index into it (`evidence_owner` for the binding check,
`evidence_index` for position). An earlier revision stored the record in
both a `TreeMap` and the `DynArray`; a status change written to one copy
was invisible through the other, and `get_evidence` returned stale
`ACTIVE` for a superseded record. Three tests caught it. The indexes now
hold pointers, never records.

## Versioning

```
E0001  version 1  ACTIVE        →  supersede  →  SUPERSEDED
E0004  version 2  ACTIVE        →  supersede  →  SUPERSEDED
E0007  version 3  ACTIVE
```

Versions increment across the chain of replacements. Ids stay
sequential across the whole agreement, so ordering is total and
unambiguous.

## What the panel sees

```json
{
  "evidence_id": "SLA-000001-E0002",
  "requirement_id": "R2",
  "evidence_type": "API_RESULT",
  "authoritative": true,
  "source_reference": "https://validator.example/api/reports/8821",
  "content_hash": "sha256:c4ca…",
  "description": "Automated validation report: 99.4% completeness…",
  "submitted_tick": 41,
  "version": 1,
  "status": "ACTIVE",
  "submitted_by_role": "provider"
}
```

`submitted_by_role` is included so the panel can weigh self-interest —
a provider's own note about their own work is exactly the kind of thing
rule 3 tells it to discount.
