# Evidence

## The trust model

```
SUBMITTER CLAIM          "I completed requirement R1."
      │                  description — shown to the panel as untrusted, never proof
      ▼
REFERENCE                source_reference — where the artifact is
      │                  + content_hash  — the identity the artifact must have
      ▼
ACTUAL ARTIFACT          fetched by EVERY node during adjudication (gl.nondet.web.get)
      │
      ▼
VERIFICATION             identity of the retrieved bytes, derived and compared in code
      │                  VERIFIED · HASH_MISMATCH · SOURCE_UNAVAILABLE · INVALID_ARTIFACT · UNSUPPORTED
      ▼
REQUIREMENT EVALUATION   the model reads VERIFIED artifacts only; code applies the evidence rule
      │
      ▼
VALIDATOR, INDEPENDENTLY fetches, verifies and evaluates again from its own retrieval
      │
      ▼
CONSENSUS                decision fingerprints compared — including which records verified
```

Three things that look alike and are not:

```
submitter description   ≠  evidence
claimed hash            ≠  verified hash
leader verification     ≠  validator verification
```

- A **description** is what a party says the artifact proves. It reaches
  the panel under the key `submitted_claim_UNTRUSTED`, beside the
  statement "The submitter's description is an untrusted claim. Do not
  treat it as proof." It can never decide a requirement: see *The
  evidence rule*.
- A **claimed hash** is the identity a party commits to. It is the thing
  retrieved bytes are compared against — never the thing trusted. A
  record whose source serves different bytes is `HASH_MISMATCH`.
- A **leader's verification** is one node's retrieval. Validators do not
  read it. Each validator fetches and verifies for itself and compares
  the result; see [CONSENSUS.md](CONSENSUS.md).

## Supported evidence types

Every type maps to exactly one acquisition method (`ACQUISITION_BY_TYPE`,
also returned by `get_protocol_info`).

| Type | Method | Reference | Committed identity |
|---|---|---|---|
| `URL` `DOCUMENT` `DATASET` `CSV` `SERVICE_LOG` `AGENT_OUTPUT` | `HTTPS_BYTES` | `https://…` without a `#fragment` | `sha256:<64 hex>` of the exact body bytes |
| `JSON` `API_RESULT` | `HTTPS_JSON` | `https://…`, optionally `#/json/pointer` | `sha256:<64 hex>` of the canonical JSON payload |
| `GITHUB_COMMIT` | `GITHUB_COMMIT` | `https://github.com/<owner>/<repo>/commit/<40-hex sha>` | `git:<the same sha>` |
| `BLOCKCHAIN_TX` `SIGNED_MESSAGE` `OTHER` | `UNSUPPORTED` | any string | any non-empty string |

### Acquisition

During `request_adjudication`, inside `gl.vm.run_nondet_unsafe`, the
leader closure and the validator closure each run:

```python
resp = gl.nondet.web.get(spec["fetch_url"])       # Response(status, headers, body: bytes)
row  = _verify_artifact(spec, resp.status, resp.body)
```

`fetch_url` is the reference (without its fragment), or the reference
plus `.patch` for a commit. The signature was checked against the std lib
bundled with the pinned runner (`py-genlayer:1jb45aa8…`,
`genlayer/gl/nondet/web.py`), not assumed from documentation. Nothing is
fetched at submission, and nothing is fetched by a frontend or backend —
there is neither.

### Canonicalisation — exactly what is hashed

**HTTPS_BYTES.** The response body, byte for byte. No decoding, no
line-ending or whitespace normalisation: the file as served is the
artifact. Headers, status and transport metadata are excluded. A source
that re-renders on every request (a dynamic HTML page, a page with ads or
timestamps) will not verify; commit a stable artifact such as a
commit-pinned raw file instead.

**HTTPS_JSON.** The body decoded as UTF-8 (a leading BOM is ignored),
parsed as JSON; if the reference carries `#/pointer`, the value it
selects under RFC 6901 (`~1` is `/`, `~0` is `~`, array indices are
non-negative integers without leading zeros); then serialised as

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

and hashed as UTF-8. Key order and whitespace therefore do not matter;
values do. The pointer is how an API result's *payload* is committed
without its transient envelope: with `#/summary`, a `generated_at` field
beside `summary` may change between fetches without breaking
verification, and any change inside `summary` does break it. Numbers are
serialised as Python's `json` module writes them (`100.0` stays `100.0`);
duplicate keys keep the last value. `scripts/evidence_identity.py`
implements these rules for submitters.

**GITHUB_COMMIT.** `<reference>.patch` is fetched. Its first line must be
`From <40-hex sha> …`; the identity is `git:<sha>`. Only a commit's
authoritative identifier is compared — not rendered HTML. What the panel
reads is the patch header and diffstat: author, date, subject, files
changed.

### Verification

`_verify_artifact(spec, http_status, body)` is pure and deterministic
over the committed spec and the bytes one node received:

| Condition | Status |
|---|---|
| type has no acquisition method | `UNSUPPORTED` — nothing is fetched |
| fetch raised, or status outside 200–299 (404, 403, 5xx…) | `SOURCE_UNAVAILABLE` — detail `HTTP <code>` or `no response` |
| empty body, body over 4 MB, body not JSON, pointer does not resolve, response not a format-patch | `INVALID_ARTIFACT` |
| derived identity ≠ committed identity | `HASH_MISMATCH` — the observed identity is recorded |
| derived identity = committed identity | `VERIFIED` |

The comparison is `observed != spec["expected"]`, in code. No model is
asked whether a hash matches. An artifact is attached to the row — and so
shown to the model — **only when it is `VERIFIED`**; a mismatched,
unavailable or invalid artifact is never shown.

## The evidence rule

Applied in code by `_bind_to_verification`, on the leader and on every
validator, after the model answers:

> A requirement is PASS or FAIL only if at least one record bound to it
> was retrieved and VERIFIED. Otherwise it is UNDETERMINED, whatever the
> model returned.

The outcome is then derived from the resulting statuses. Consequences:

- a description, a reference string or a claimed hash cannot move a
  requirement in either direction;
- a requirement whose evidence is unavailable, mismatched, invalid,
  unsupported — or absent — is UNDETERMINED, so the agreement is
  UNDETERMINED and **cannot settle**;
- if no record verifies at all, no model is consulted: `_unverified_verdict`
  marks every requirement UNDETERMINED and the stored `raw_json` is `{}`.

UNDETERMINED keeps escrow whole. Its exits are unchanged: more evidence
and another round, or `recover_escrow` to the client after the resolution
deadline. A provider who wants a partial payout must therefore make every
requirement's evidence verifiable — including, for a requirement they
failed, letting the client's verifiable evidence of that failure stand.

`deadline_met` may be `false` only on a VERIFIED artifact showing late
delivery; with nothing verified it is `true`, since a penalty needs
positive proof.

## What each round records

`get_verdict` returns, alongside the verdict:

- `evidence_verification` — one row per acquired record: `evidence_id`,
  `requirement_id`, `evidence_type`, `acquisition`, `source_reference`,
  `expected_identity`, `status`, `observed_identity`, `byte_length`,
  `detail`. No artifact content is stored (§34 of the steward brief).
- `evidence_commitment_hash` — sha256 over the committed records the
  round acquired: id, requirement, type, reference, identity, version,
  status.

The rows are the leader's observations; whether each record verified is
agreed by every validator (see CONSENSUS.md), and after consensus the
contract checks that the rows describe exactly the committed records and
that no decided requirement lacks a verified record.

## Provenance — what a match does and does not prove

A `VERIFIED` byte or JSON artifact proves that **the source served, at
adjudication time, exactly the content the submitter committed to**. It
does not prove who authored that content, that the host is honest, or
that the content is true. A commit identity proves that **GitHub serves
that commit id for that repository**; the author and date inside it are
git metadata, which GitHub does not authenticate. The panel judges what
the verified artifact shows; the verification only guarantees it is the
committed artifact.

## Unsupported types, and why

- **`BLOCKCHAIN_TX`** — reading a transaction needs a chain-specific RPC
  endpoint. Whoever picks that endpoint becomes the oracle for it, and the
  contract has no neutral way to pick one. A party can instead commit an
  explorer's JSON API response as `API_RESULT`, which verifies that
  response's content — with that explorer as its provenance.
- **`SIGNED_MESSAGE`** — verifying a signature needs secp256k1 recovery.
  The pinned runner's manifest depends on CPython, cloudpickle and the
  GenLayer std lib only, none of which implements it; a hand-written curve
  implementation inside the contract would be unaudited cryptography.
  Hashing the message text would verify nothing about the signature.
- **`OTHER`** — no defined artifact, so nothing to acquire.

Unsupported records may still be submitted, so a party can put something
on the record, and they are shown to the panel with status `UNSUPPORTED`
and no content. They can never be the reason a requirement is decided.

## Submission rules

`submit_evidence` and `supersede_evidence` refuse, before anything is
stored:

- an unknown requirement or a type outside the vocabulary;
- an empty identity, or one longer than 128 characters;
- a reference longer than 512 characters — refused, never truncated,
  because a shortened URL names a different artifact;
- for a fetchable type: a reference that is not `https://`, contains
  whitespace, or (for byte types) carries a `#fragment`; an identity that
  is not `sha256:<64 hex>`;
- for `GITHUB_COMMIT`: a reference that is not a full 40-hex commit URL, or
  an identity other than `git:<that sha>`;
- for `HTTPS_JSON`: a fragment that is not a JSON Pointer.

Identities are stored lowercase.

## Immutability and versioning

**No method mutates a stored reference, identity, type, agreement or
requirement.** There is no `update_evidence`, `edit_evidence`,
`set_evidence_hash` or `delete_evidence`;
`test_C_committed_evidence_cannot_be_edited` asserts their absence.

Replacement is explicit and additive:

```
supersede_evidence(agreement_id, evidence_id, new_reference, new_identity, desc)

  old record:  ACTIVE → SUPERSEDED   reference and identity unchanged, still readable
  new record:  fresh evidence_id, same requirement and type, version + 1, ACTIVE
```

Superseded records are not acquired. An adjudication round runs inside
one transaction, so a record cannot change between the leader's fetch and
a validator's — the committed record is fixed for the whole round, and
each round acquires afresh: nothing verified in an earlier round is
reused (`test_H_artifact_that_changes_after_a_verdict_is_reacquired_and_fails`).

`challenge_evidence` marks a record `CHALLENGED` and appends a note; the
record is still acquired and verified like any other.

## Agreement binding

Every evidence access goes through `_resolve_evidence(evidence_id,
expected_agreement_id)`, which refuses a record belonging to another
agreement even when the caller is a party to both. A round acquires only
the agreement's own records (`_adjudication_specs`), and after consensus
every cited id is checked against that set. A public URL committed to
agreement A is not evidence for agreement B unless B's own party commits
it to B (`test_E_evidence_of_another_agreement_is_never_acquired_or_usable`).
