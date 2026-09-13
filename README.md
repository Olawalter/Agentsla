# AgentSLA Core

> A reusable GenLayer Intelligent Contract primitive for evidence-based
> service agreements: lock an agreement and real GEN on chain, commit
> evidence as a reference and an identity, have every validator retrieve
> and verify the actual artifacts and decide whether the requirements were
> met, wait for finality, and settle deterministically.

The contract is the artifact. There is no frontend, and the project
loses nothing without one.

---

## The problem

A deterministic contract enforces this perfectly:

```python
if timestamp > deadline:
    transfer()
```

It cannot establish this at all:

> Did the provider actually satisfy this natural-language requirement,
> given the evidence submitted?

That question decides most real agreements — agent-to-agent work, API
SLAs, data delivery, bounties, milestone work, procurement. Today it is
answered by a human, a platform, or nobody.

## The design

AgentSLA Core splits the two jobs and gives each to the layer that can
actually do it.

```
      GenLayer validator consensus              Deterministic contract code
      decides FACTS                             decides MONEY

      per-requirement PASS / FAIL /             earned_weight × escrow ÷ 100
        UNDETERMINED                            penalty if deadline missed
      deadline_met                              client_refund = escrow − payout
      which evidence was examined               escrow zeroing, the transfer

      Deterministic code, on every node, also decides WHETHER EVIDENCE COUNTS:
      the retrieved bytes match the committed identity, or the record
      supports nothing.
```

The adjudicating model is **never allowed to name an amount**. It
returns a structured verdict over the committed requirement set; the
contract multiplies the earned weight by the real escrow balance. A
verdict carrying `provider_payout: 999999999999` changes nothing,
because no code path reads such a field. That is not a policy — it is a
property of `_normalize_verdict`, which returns a fixed key set.

## Why GenLayer

Everything except the judgment could run on any chain. The judgment
cannot: it needs a model to read unstructured evidence, and it needs
more than one independent party to agree on what that evidence shows.

GenLayer gives both. `request_adjudication` runs
`gl.vm.run_nondet_unsafe`, where the leader **and every validator**
independently fetch every committed evidence reference with
`gl.nondet.web.get`, verify the bytes against the committed identity,
judge only the artifacts that verified, and compare decision
fingerprints. Agreement means several nodes that each retrieved the
evidence themselves reached the same factual conclusions — not that one
node's JSON parsed, and not that anyone read a description.

## Lifecycle

```
DRAFT ──fund──► FUNDED ──accept──► ACTIVE ──deliver──► SUBMITTED
                                                            │
                                              request_adjudication
                                                            │
                                        ┌───────────────────┴──────────────┐
                                        ▼                                  ▼
                                  UNDETERMINED                        ACCEPTED
                                  escrow frozen                    appeal window
                                        │                                  │
                     more evidence ─────┤                    ┌─────────────┴────────┐
                     re-adjudicate      │                 appeal                finalize
                                        │                    │                     │
                     recover_escrow ────┘                 APPEALED            FINALIZED
                             │                               │                     │
                             ▼                    re-adjudicate                 settle
                         REFUNDED                                                  │
                                                                                   ▼
                                                                               SETTLED
```

`ACCEPTED` is **not** `FINALIZED`. A verdict accepted by consensus is
not yet spendable; it becomes spendable only after the window in which a
party could contest it has actually elapsed. Appealing moves the
agreement out of `ACCEPTED` entirely, so a stale accepted verdict can
never trigger settlement.

## Evidence

A submitter cannot make a claim into evidence by supplying a description,
a URL or a hash.

```
SUBMITTER CLAIM      "Requirement completed."   shown to the panel as UNTRUSTED
REFERENCE + IDENTITY https://… + sha256:…       what was committed — not proof
ACTUAL ARTIFACT      fetched by the leader AND every validator during adjudication
VERIFICATION         identity of the retrieved bytes compared with the commitment, in code
EVALUATION           the model reads VERIFIED artifacts only
```

| Type | Acquisition | Identity |
|---|---|---|
| `URL` `DOCUMENT` `DATASET` `CSV` `SERVICE_LOG` `AGENT_OUTPUT` | HTTPS GET | `sha256:` of the exact body bytes |
| `JSON` `API_RESULT` | HTTPS GET, optional `#/json/pointer` | `sha256:` of the canonical JSON payload |
| `GITHUB_COMMIT` | `<commit url>.patch` | `git:<sha>` — the commit id GitHub serves |
| `BLOCKCHAIN_TX` `SIGNED_MESSAGE` `OTHER` | **UNSUPPORTED** | recorded, never decides anything |

Each record verifies as `VERIFIED`, `HASH_MISMATCH`, `SOURCE_UNAVAILABLE`,
`INVALID_ARTIFACT` or `UNSUPPORTED`. The **evidence rule**, applied in code
on every node: a requirement is PASS or FAIL only if a record bound to it
VERIFIED; otherwise it is UNDETERMINED, whatever the model said, and an
UNDETERMINED agreement cannot settle. If nothing verifies, no model is
consulted at all.

`scripts/evidence_identity.py` prints the identity to commit for a
reference. It is a convenience with no authority: the contract derives
the identity again from its own retrieval.

Committed records are never mutated; `supersede_evidence` is additive and
superseded records are not acquired. See [docs/EVIDENCE.md](docs/EVIDENCE.md)
for canonicalisation, provenance and failure handling.

## Consensus

The consensus-critical projection is:

```
agreement_id · outcome · per-requirement statuses (after the evidence rule) ·
deadline_met · evidence_examined · per-record "verified"
```

A validator does not read the leader's verification, artifact, reasoning
or verdict as input. It fetches, verifies and judges for itself, and
compares the result. `reasoning` is excluded, because two honest
validators do not write the same paragraph; the *kind* of verification
failure is excluded, because an unavailable source and a mismatched one
have the same consequence. See [docs/CONSENSUS.md](docs/CONSENSUS.md).

## Settlement

```
earned_weight   = Σ weight[r] for every requirement marked PASS
provider_gross  = escrow × earned_weight ÷ 100        (integer floor)
penalty         = provider_gross × penalty_bps ÷ 10000  only if deadline missed
provider_net    = max(0, provider_gross − penalty)
client_refund   = escrow − provider_net
```

Integer arithmetic throughout; no float touches a weight or an amount.
Rounding remainders go to the **client**, because `client_refund` is
computed by subtraction — the party owed a refund is never short by a
rounding artefact, and the provider cannot gain from one.

Worked: 100 GEN escrow, R1 (40) PASS, R2 (30) PASS, R3 (30) FAIL →
provider **70 GEN**, client **30 GEN**, escrow **0**.
See [docs/SETTLEMENT.md](docs/SETTLEMENT.md).

## Escrow

Real custody, not an accounting variable. `fund_agreement` is
`@gl.public.write.payable` and records `gl.message.value` — the figure
the chain actually moved, never a caller-supplied argument. Under- and
over-funding are both refused.

All value leaves through one audited helper, `_send_gen`, on three
paths only: `settle`, `cancel_agreement`, `recover_escrow`. Every one
follows the same order:

```
read ledger → validate → compute → zero ledger → persist → emit transfer
```

## Reuse

Nothing in the contract knows about any industry. A consumer supplies
the requirements, weights, deadlines and rules; the contract hashes them
into a commitment and adjudicates against that frozen version.

**Agent work**
```json
[{"requirement_id":"R1","description":"Produce the requested analysis as a written report","weight":60},
 {"requirement_id":"R2","description":"Cite at least five primary sources","weight":40}]
```

**API SLA**
```json
[{"requirement_id":"R1","description":"p99 latency stayed under 250ms for the billing period","weight":50},
 {"requirement_id":"R2","description":"Uptime was at least 99.9%","weight":50}]
```

**Data delivery**
```json
[{"requirement_id":"R1","description":"Deliver the requested dataset","weight":40},
 {"requirement_id":"R2","description":"Dataset meets the agreed quality bar","weight":30},
 {"requirement_id":"R3","description":"Delivery before the deadline","weight":30}]
```

Same contract, no modification. Weights must be integers summing to
exactly 100.

## Quick start

```bash
pip install -r requirements.txt

genvm-lint check contracts/agentsla_core.py     # lint
pytest tests/direct/ -v                         # 137 tests, offline
SKIP_INTEGRATION=0 pytest tests/integration -v -s   # live StudioNet, no keys needed
```

The live suite generates throwaway accounts, funds them from the StudioNet
faucet, deploys the contract and runs both scenarios below. Set
`AGENTSLA_CONTRACT=<address>` to run against an existing deployment
instead.

## Complete example

```python
# client creates
aid = create_agreement(
    provider            = "0xF50e…6A90",
    service_description = "Deliver the ACME July 2026 daily dataset with a quality report, by 2026-09-01T00:00:00Z.",
    requirements_json   = json.dumps([
        {"requirement_id": "R1", "description": "Deliver ACME daily close and volume for July 2026 as a CSV", "weight": 40},
        {"requirement_id": "R2", "description": "A quality check reports ≥ 99% field completeness",          "weight": 30},
        {"requirement_id": "R3", "description": "Delivered on or before 2026-09-01T00:00:00Z",               "weight": 30},
    ]),
    payment_amount_atto        = 10**17,
    acceptance_deadline_ticks  = 10,
    service_deadline_ticks     = 30,
    resolution_deadline_ticks  = 200,
)

fund_agreement(aid)            # client, payable, exact amount — terms LOCK here
accept_agreement(aid)          # provider — designated wallet only

# provider commits a REFERENCE and an IDENTITY for each requirement
submit_evidence(aid, "R1", "DATASET",       ".../acme-2026-07-daily.csv",        "sha256:6ae1c7fd…")
submit_evidence(aid, "R2", "API_RESULT",    ".../quality-report.json#/summary",  "sha256:0495cf67…")
submit_evidence(aid, "R3", "GITHUB_COMMIT", ".../commit/8517e9ab…",              "git:8517e9ab…")
submit_deliverable(aid)        # a CLAIM — advances state, proves nothing

request_adjudication(aid)      # every node fetches all three, all VERIFIED
                               # → PARTIAL: R1 PASS, R2 PASS, R3 FAIL (commit dated 2026-09-13)
                               # → ACCEPTED, appeal window opens

settle(aid)                    # REFUSED — ACCEPTED is not FINALIZED
tick(); tick(); tick(); tick()
finalize(aid)                  # → FINALIZED
settle(aid)                    # provider 70%, client 30%, escrow 0, SETTLED
```

## Proven live on StudioNet

`tests/integration/test_end_to_end.py`, run against a real validator
panel. The evidence is real: files served from commit
[`8517e9ab`](https://github.com/Olawalter/Agentsla/commit/8517e9ab0848558b790cee8f8c9a0e533ec7cc3a)
of this repository (branch `live-evidence`) — a daily price CSV, a quality
report about it, and the commit that delivered them, dated after the
agreement's stated deadline. The descriptions the provider committed say
nothing useful ("Dataset delivered.", "Delivered on time."); the verdict had
to come from the artifacts.

- Contract: `0x6a03Baf33dC24fBC0a7C1ceC47C511A740CddED9`, byte-identical to
  `contracts/agentsla_core.py` in this commit (sha256 of the LF source
  `881d91680b0c340cf1e6ca403a102d7089ea715e902e9aa048bcbd536963d8e6`)
- Deploy tx: `0xdcad5c60121fda7876629433b34ffc661446713494d7e4fcdf3044a8a32b903f`
- Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
- Every transaction, decision and refusal: [docs/live-run.json](docs/live-run.json)

### SLA-000001 — verified evidence, PARTIAL, settled 70/30

| Step | Tx | Result |
|---|---|---|
| `create_agreement` → `fund_agreement` (0.1 GEN) → `accept_agreement` | `0x98150006…`, `0x6a210e91…`, `0x12441244…` | MAJORITY_AGREE |
| `submit_evidence` R1 / R2 / R3 | `0xae8673ec…`, `0x77abf5bb…`, `0x5dbf5248…` | MAJORITY_AGREE |
| `submit_deliverable` | `0x844583f4…` | MAJORITY_AGREE |
| **`request_adjudication`** | `0x146c1e8b32298e247f712a993755835c4a254c747bdbea556b46b22686e106cd` | MAJORITY_AGREE, round 1 |
| `settle` **while ACCEPTED** | `0x6ac7fd02…` | **REFUSED** `illegal transition from ACCEPTED` |
| `tick` ×4 → `finalize` | `0xfb6b5d6d…` … `0x3c42e91d…` | MAJORITY_AGREE |
| `settle` | `0xadf3b21e…` | FINALIZED |

```
evidence_verification   R1 DATASET        VERIFIED  sha256:6ae1c7fd… = sha256:6ae1c7fd…
                        R2 API_RESULT     VERIFIED  sha256:0495cf67… = sha256:0495cf67…
                        R3 GITHUB_COMMIT  VERIFIED  git:8517e9ab…    = git:8517e9ab…
outcome                 PARTIAL      R1 PASS   R2 PASS   R3 FAIL     deadline_met false
earned_weight           70/100       ← derived by the CONTRACT
settlement              provider 70000000000000000   client 30000000000000000   escrow 0
wallets                 provider balance +70000000000000000, contract −100000000000000000
```

> *"R3: The verified artifact SLA-000001-E0003 shows the delivery commit
> date was Sun, 13 Sep 2026. This is after the stated deadline of
> 2026-09-01T00:00:00Z. Status: FAIL."*

### SLA-000002 — nothing verifiable, UNDETERMINED

Same agreement, evidenced badly: R1 commits the wrong hash for the real
dataset, R2 points at a file that does not exist, R3 is a signed message.
Every description says "Requirement completed."

| Step | Tx | Result |
|---|---|---|
| **`request_adjudication`** | `0xf4f666d1b0eae05ce71dee173facd734e490eecb85463067b44ac464f405df39` | MAJORITY_AGREE, round 1 |
| `settle` | `0xd2d3b41a…` | **REFUSED** `illegal transition from UNDETERMINED` |
| `finalize` | `0x803a0e7e…` | **REFUSED** `illegal transition from UNDETERMINED` |

```
evidence_verification   R1  HASH_MISMATCH       observed sha256 of the real file ≠ committed
                        R2  SOURCE_UNAVAILABLE  HTTP 404
                        R3  UNSUPPORTED         no acquisition method for SIGNED_MESSAGE
outcome                 UNDETERMINED on every requirement
raw_json                {}        ← no model was consulted
escrow                  100000000000000000, untouched
```

### What the first live attempt taught

The first run of this suite, on a disposable deployment
(`0xb85F75664cdc03Ee42e8CD19961dDb376c4Edf5B`), acquired and verified all
three artifacts correctly — and the panel still split over four rounds
(`0xbf4d53fd5ee1a2c2fc2e43849651712ba189bb6ecf3ed27a7855e86c77c86e46`,
MAJORITY_DISAGREE). The leader's own output showed why: it wrote
`R3: PASS, deadline_met: true`, then reasoned in the `reasoning` field to
*"R3 FAILS … Outcome is PARTIAL"*. The prompt listed `reasoning` last, so
models committed statuses before thinking, and its `deadline_met` rule
read as if it governed a timing requirement. The prompt now asks for
reasoning first, says a timing requirement is judged like any other, and
separates `deadline_met` from requirement status. A validator that
disagrees now prints its own decision fingerprint to its receipt stdout,
so a future split can be read from the chain.

### The deployment this replaces

`0xBbDC33708DD50E8FA1854F5769B4Df598a43f377` ran the previous version,
whose adjudication never retrieved evidence: the panel judged the
submitted descriptions, references and hashes as text. Its recorded
verdicts rest on those descriptions (the R3 "late delivery" was stated in
a description, and the references were placeholder `*.example` URLs). It
should not be used.

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | layers, state machine, storage shape, where value can leave |
| [CONSENSUS.md](docs/CONSENSUS.md) | leader and validator code paths, validator independence, the decision fingerprint |
| [EVIDENCE.md](docs/EVIDENCE.md) | the trust model: supported types, acquisition, canonicalisation, verification, the evidence rule |
| [SETTLEMENT.md](docs/SETTLEMENT.md) | the formula, rounding, invariants, the three exits |
| [SECURITY.md](docs/SECURITY.md) | attacks A–I and the evidence-trust attacks, each mapped to its defence and test |
| [CONTRACT_API.md](docs/CONTRACT_API.md) | every method: purpose, caller, state, failures |
| [TESTING.md](docs/TESTING.md) | the five layers and how to run them |

## Testing

```
genvm-lint check              passes — 26 methods (10 view, 16 write)
pytest tests/direct           137 passed
pytest tests/integration      2 passed on StudioNet, real panel (6m27s)
mutation sweep                12/12 evidence-trust defences broken on purpose, all caught
```

Five layers — state, escrow, evidence, adjudication, equivalence — plus
attacks A–I, the three required scenarios, and
`tests/direct/test_evidence_verification.py`: real bytes served through
mocked sources and verified by the contract itself, false descriptions,
wrong and modified hashes, unavailable and invalid sources, unsupported
types, cross-agreement evidence, and — by replaying the contract's own
validator closure with `direct_vm.run_validator()` — validators that fetch
for themselves and refuse a leader claiming PASS and verified. Every
adversarial test asserts that **money did not move**, not merely that a
status changed.

## Known limitations

- **A verified artifact is the committed artifact, not a true one.**
  Verification proves the source served exactly the bytes the submitter
  committed to. It does not prove who wrote them: a provider can host a
  fabricated report and it will verify. The panel then judges what that
  artifact shows. A commit identity proves GitHub serves that commit; its
  author and date are unauthenticated git metadata.
- **Unstable sources cannot verify.** A page that changes on every request
  is `HASH_MISMATCH` by design. Commit stable artifacts — commit-pinned raw
  files, or a JSON payload selected by pointer.
- **Unsupported types.** Chain transactions and signed messages are
  recorded but cannot decide a requirement: reading a transaction needs an
  RPC someone would have to choose, and the runner has no secp256k1
  implementation. See docs/EVIDENCE.md.
- **Every requirement must be evidenced verifiably to settle.** A
  requirement with no verified record is UNDETERMINED, which blocks
  settlement until more evidence is adjudicated or `recover_escrow` refunds
  the client after the resolution deadline.
- **Canonical JSON follows Python's `json` module** — for example `100.0`
  stays `100.0`. Submitters in other languages should compute identities
  with `scripts/evidence_identity.py`.
- **The clock is a tick counter, not a wall clock.** Deadlines are
  absolute tick values and `tick()` is public, so deadlines advance with
  protocol activity rather than elapsed time. Calendar deadlines that
  evidence must be judged against belong in the agreement's text.
- **Panel capture is out of scope.** A compromised validator majority
  can agree on a false verdict; that is GenLayer's trust model. The
  appeal path exists so a bad round can be contested and re-run.
- **No frontend.** Deliberate — the primitive is the deliverable.
