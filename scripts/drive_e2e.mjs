/**
 * AgentSLA Core — live end-to-end driver.
 *
 * Runs the spec's canonical scenario against a real deployment with a
 * real validator panel:
 *
 *   client creates -> funds 100 GEN -> provider accepts -> provider
 *   submits evidence -> declares delivery -> GenLayer adjudicates ->
 *   appeal window elapses -> finalize -> settle
 *
 * The evidence is authored so that R1 and R2 are supported and R3
 * (delivery before deadline) is not: the delivery commit is dated after
 * the agreed deadline and says so plainly. A correct panel returns
 * PARTIAL with R3 FAIL, and the contract then pays 70/30 from the
 * committed weights — no amount is ever asked of the model.
 *
 * Usage:
 *   AGENTSLA_CLIENT_KEY=0x… AGENTSLA_PROVIDER_KEY=0x… \
 *     node scripts/drive_e2e.mjs <contract_address> [--undetermined]
 */
import { createClient } from "genlayer-js";
import { studionet } from "genlayer-js/chains";
import { privateKeyToAccount } from "viem/accounts";

const CONTRACT = process.argv[2];
if (!CONTRACT) {
  console.error("usage: node scripts/drive_e2e.mjs <contract_address> [--undetermined]");
  process.exit(2);
}
const WANT_UNDETERMINED = process.argv.includes("--undetermined");

const CLIENT_KEY = process.env.AGENTSLA_CLIENT_KEY;
const PROVIDER_KEY = process.env.AGENTSLA_PROVIDER_KEY;
if (!CLIENT_KEY || !PROVIDER_KEY) {
  console.error("set AGENTSLA_CLIENT_KEY and AGENTSLA_PROVIDER_KEY");
  process.exit(2);
}

// Small enough to fund two throwaway accounts, large enough that the
// 70/30 split is exact and legible.
const PRICE = 100_000_000_000_000_000n;      // 0.1 GEN

const REQUIREMENTS = [
  {
    requirement_id: "R1",
    description: "Deliver the requested market dataset as a downloadable file.",
    weight: 40,
    required: true,
    evidence_rule: "A dataset reference with a content hash.",
  },
  {
    requirement_id: "R2",
    description: "The delivered dataset meets the agreed quality bar: at least 99% field completeness.",
    weight: 30,
    required: true,
    evidence_rule: "An automated validation report or API result stating completeness.",
  },
  {
    requirement_id: "R3",
    description: "Delivery occurred on or before the agreed service deadline.",
    weight: 30,
    required: false,
    evidence_rule: "A timestamped commit or receipt dated on or before the deadline.",
  },
];

// genlayer-js wants an Account OBJECT here. Handing it a raw private-key
// string makes it treat the key itself as the sender address, which
// surfaces much later as an opaque "Address is invalid" on the first
// write. Derive the account once, up front.
const ACCOUNTS = new Map();
function account(pk) {
  if (!ACCOUNTS.has(pk)) ACCOUNTS.set(pk, privateKeyToAccount(pk));
  return ACCOUNTS.get(pk);
}

function client(pk) {
  return createClient({ chain: studionet, account: account(pk) });
}

function leaderReceipts(r) {
  const direct = r?.consensus_data?.leader_receipt;
  if (Array.isArray(direct)) return direct;
  const nested = r?.result?.leader_receipt ?? r?.leader_receipt;
  if (Array.isArray(nested)) return nested;
  return [];
}

function revertReason(r) {
  for (const lr of leaderReceipts(r)) {
    const res = lr?.result;
    if (String(res?.status ?? "") === "rollback" && typeof res?.payload === "string") {
      return res.payload;
    }
  }
  return null;
}

function returned(r) {
  for (const lr of leaderReceipts(r)) {
    const res = lr?.result;
    if (String(res?.status ?? "") !== "return") continue;
    let v = res?.payload?.readable ?? res?.payload;
    for (let i = 0; i < 3 && typeof v === "string"; i++) {
      try { v = JSON.parse(v); } catch { return v; }
    }
    return v;
  }
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const asText = (e) => String(e?.message || e?.details || e || "");
const isRateLimit = (e) => /Rate limit|-32429|429/.test(asText(e));

// StudioNet intermittently answers a poll with an HTML error page or
// drops the connection outright. Both surface here as opaque viem
// errors. They are infrastructure, not contract failures, and the
// transaction they were polling for is ALREADY SUBMITTED — aborting
// would strand it and re-sending would double-spend the action.
const isTransientRpc = (e) => {
  const t = asText(e);
  return /fetch failed|ECONNRESET|ETIMEDOUT|ENOTFOUND|socket hang up/.test(t)
    || /Unexpected token '<'|not valid JSON/.test(t)     // HTML error page
    || /\b(502|503|504)\b/.test(t);
};

async function retrying(fn, label) {
  for (let i = 0; ; i++) {
    try { return await fn(); }
    catch (e) {
      if (isRateLimit(e) && i < 5) {
        const wait = 60_000 + i * 30_000;
        console.log(`\n    ${label}: rate-limited, sleeping ${Math.round(wait / 1000)}s…`);
        await sleep(wait);
        continue;
      }
      if (isTransientRpc(e) && i < 8) {
        const wait = 10_000 + i * 5_000;
        console.log(`\n    ${label}: transient RPC (${asText(e).slice(0, 60)}), `
                    + `retrying in ${Math.round(wait / 1000)}s…`);
        await sleep(wait);
        continue;
      }
      throw e;
    }
  }
}

async function read(fn, args = []) {
  const c = client(CLIENT_KEY);
  const raw = await retrying(() => c.readContract({
    address: CONTRACT, functionName: fn, args,
  }), `read ${fn}`);
  return typeof raw === "string" ? JSON.parse(raw) : raw;
}

async function write(pk, fn, args = [], opts = {}) {
  const c = client(pk);
  const params = { address: CONTRACT, functionName: fn, args };
  if (opts.value !== undefined) params.value = opts.value;
  const hash = await retrying(() => c.writeContract(params), `write ${fn}`);
  process.stdout.write(`    ${fn.padEnd(22)} → ${hash} `);
  const receipt = await retrying(() => c.waitForTransactionReceipt({
    hash, status: opts.wait ?? "ACCEPTED",
    retries: opts.wait === "FINALIZED" ? 80 : 60,
    interval: 10_000,
  }), `wait ${fn}`);
  const reason = revertReason(receipt);
  if (reason) {
    console.log("REVERTED");
    throw new Error(`${fn}: ${reason}`);
  }
  console.log("ok");
  return receipt;
}

async function main() {
  const clientAddr = account(CLIENT_KEY).address;
  const providerAddr = account(PROVIDER_KEY).address;

  console.log(`CLIENT   ${clientAddr}`);
  console.log(`PROVIDER ${providerAddr}`);
  console.log(`CONTRACT ${CONTRACT}`);
  console.log(`SCENARIO ${WANT_UNDETERMINED ? "UNDETERMINED (ambiguous R2)" : "PARTIAL (R3 late)"}`);
  console.log();

  // ── 1 · create ──
  console.log("STEP • create_agreement (client)");
  let r = await write(CLIENT_KEY, "create_agreement", [
    providerAddr,
    "Deliver a cleaned Q3 2026 market dataset with an automated quality report.",
    JSON.stringify(REQUIREMENTS),
    PRICE.toString(),
    10,        // acceptance deadline (ticks)
    30,        // service deadline
    200,       // resolution deadline
    "Prefer authoritative evidence: datasets, API results, signed commits.",
    "Weighted per-requirement payout. Rounding remainder to the client.",
    0,         // penalty_bps
    3,         // appeal window ticks
  ]);
  const agreementId = returned(r);
  console.log(`    → ${agreementId}\n`);

  // ── 2 · fund ──
  console.log("STEP • fund_agreement (client, exact price)");
  await write(CLIENT_KEY, "fund_agreement", [agreementId], { value: PRICE });
  let a = await read("get_agreement", [agreementId]);
  console.log(`    → ${a.status}, escrow ${a.escrow_deposited}, terms_locked ${a.terms_locked}`);
  console.log(`    → terms_hash ${a.terms_hash.slice(0, 24)}…\n`);

  // ── 3 · accept ──
  console.log("STEP • accept_agreement (designated provider)");
  await write(PROVIDER_KEY, "accept_agreement", [agreementId]);
  console.log();

  // ── 4 · evidence ──
  console.log("STEP • submit_evidence ×3 (provider)");
  await write(PROVIDER_KEY, "submit_evidence", [
    agreementId, "R1", "DATASET",
    "https://data.example/market-q3-2026.parquet",
    "sha256:8f14e45fceea167a5a36dedd4bea2543b3e1f4ea0e0c2d9f2b9a5e7c1d0a3b6e",
    "Cleaned Q3 2026 market dataset, 2,140,882 rows, parquet, 812 MB.",
  ]);
  await write(PROVIDER_KEY, "submit_evidence", [
    agreementId, "R2", "API_RESULT",
    "https://validator.example/api/reports/8821",
    "sha256:c4ca4238a0b923820dcc509a6f75849b1f2e3d4c5b6a7988990011223344556677",
    "Automated validation report: field completeness 99.4%, schema conformant, "
    + "0 duplicate primary keys. Meets the agreed 99% quality bar.",
  ]);
  if (WANT_UNDETERMINED) {
    // Deliberately unverifiable: the panel is expected to say so.
    await write(PROVIDER_KEY, "submit_evidence", [
      agreementId, "R3", "OTHER",
      "internal note",
      "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      "Provider states delivery was on time. No timestamp, receipt or commit "
      + "is attached and no external reference is given.",
    ]);
  } else {
    // Plainly late — a correct panel fails R3 on this.
    await write(PROVIDER_KEY, "submit_evidence", [
      agreementId, "R3", "GITHUB_COMMIT",
      "https://github.com/example/delivery/commit/9fe1c2b",
      "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "Delivery commit. IMPORTANT: this commit is dated 2026-10-04, which is "
      + "AFTER the agreed service deadline of 2026-09-30. Delivery was four "
      + "days late.",
    ]);
  }
  const ev = await read("get_evidence", [agreementId]);
  console.log(`    → ${ev.length} records: ${ev.map((e) => e.evidence_id).join(", ")}\n`);

  // ── 5 · declare delivery ──
  console.log("STEP • submit_deliverable (provider — a CLAIM, not evidence)");
  await write(PROVIDER_KEY, "submit_deliverable", [agreementId, "Dataset delivered."]);
  console.log();

  // ── 6 · adjudicate ──
  console.log("STEP • request_adjudication — LIVE GenLayer validator panel");
  console.log("    (each validator independently evaluates the evidence…)");
  await write(CLIENT_KEY, "request_adjudication", [agreementId], { wait: "FINALIZED" });
  a = await read("get_agreement", [agreementId]);
  const v = await read("get_verdict", [agreementId, a.latest_judgment_id ?? a.latest_verdict_id]);
  console.log(`    → verdict #${v.verdict_id}: ${v.outcome}, earned_weight ${v.earned_weight}/100`);
  for (const rr of v.requirements) console.log(`        ${rr.requirement_id}: ${rr.status}`);
  console.log(`    → deadline_met ${v.deadline_met}`);
  console.log(`    → examined ${JSON.stringify(v.evidence_examined)}`);
  console.log(`    → reasoning: ${String(v.reasoning).slice(0, 220)}`);
  console.log(`    → agreement status ${a.status}\n`);

  if (a.status === "UNDETERMINED") {
    console.log("────────────── UNDETERMINED — escrow protected ──────────────");
    const esc = await read("get_escrow", [agreementId]);
    console.log(`  escrow_available  ${esc.available}   (unchanged: ${esc.available === esc.deposited})`);
    console.log(`  status            ${a.status}`);
    console.log(`  settle() and finalize() are both illegal from here.`);
    return;
  }

  // ── 7 · finality: prove ACCEPTED cannot settle ──
  console.log("STEP • finality gate");
  try {
    await write(CLIENT_KEY, "settle", [agreementId]);
    throw new Error("SETTLED FROM ACCEPTED — finality gate is broken!");
  } catch (e) {
    if (/illegal transition from ACCEPTED/.test(String(e.message))) {
      console.log("    ✓ settle() correctly refused while merely ACCEPTED");
    } else { throw e; }
  }

  console.log("    ticking past the appeal window…");
  for (let i = 0; i < 4; i++) await write(CLIENT_KEY, "tick", []);
  await write(CLIENT_KEY, "finalize", [agreementId], { wait: "FINALIZED" });
  const st = await read("get_state", [agreementId]);
  console.log(`    → ${st.status}, can_settle ${st.can_settle}\n`);

  // ── 8 · settle ──
  console.log("STEP • settle (deterministic, from committed weights)");
  await write(CLIENT_KEY, "settle", [agreementId], { wait: "FINALIZED" });

  a = await read("get_agreement", [agreementId]);
  const s = await read("get_settlement", [agreementId]);
  const esc = await read("get_escrow", [agreementId]);

  console.log("\n────────────── DONE ──────────────");
  console.log(`  agreement         ${agreementId}`);
  console.log(`  status            ${a.status}`);
  console.log(`  verdict           #${s.verdict_id} (${v.outcome})`);
  console.log(`  earned_weight     ${s.earned_weight}/${s.total_weight}`);
  console.log(`  escrow_before     ${s.escrow_before}`);
  console.log(`  provider_payout   ${s.provider_payout}`);
  console.log(`  client_refund     ${s.client_refund}`);
  console.log(`  penalty           ${s.penalty}`);
  console.log(`  escrow_after      ${s.escrow_after}`);
  console.log(`  escrow_available  ${esc.available}`);
  // u256 fields arrive as strings — adding them with `+` would
  // concatenate, so compare as BigInt.
  const balances =
    BigInt(s.provider_payout) + BigInt(s.client_refund) === BigInt(s.escrow_before);
  console.log(`  balances exactly  ${balances}`);
  if (!balances) {
    console.error("  !! settlement does not balance — this is a contract bug");
    process.exitCode = 1;
  }
}

main().catch((e) => {
  console.error("\n!!! e2e failed:", e.message || e);
  process.exit(1);
});
