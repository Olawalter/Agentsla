"""Live-network harness for the integration suite.

    SKIP_INTEGRATION=0 pytest tests/integration -v -s

Needs no keys: it generates throwaway client and provider accounts and
funds them from the StudioNet faucet. It deploys the contract from the
working tree (line endings normalised to what git stores) unless
AGENTSLA_CONTRACT names an existing deployment of the same source.

Every transaction is recorded to docs/live-run.json: hash, the
validators' decision, the leader's execution result, and — for a
revert — the contract's own refusal message.
"""
import json
import os
import pathlib
import time
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "agentsla_core.py"
RECORD = ROOT / "docs" / "live-run.json"
RPC = "https://studio.genlayer.com/api"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

LIVE = os.environ.get("SKIP_INTEGRATION", "1") == "0"


def rpc(method, params, attempts=8):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    for i in range(attempts):
        try:
            req = urllib.request.Request(RPC, data=body, headers={
                "Content-Type": "application/json", "User-Agent": UA})
            out = json.load(urllib.request.urlopen(req, timeout=120))
            if "error" in out:
                raise RuntimeError(f"{method}: {out['error']}")
            return out["result"]
        except RuntimeError:
            raise
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(5 + 5 * i)


def _patch_transport():
    """The public RPC drops connections and answers with CDN error pages
    mid-poll. Retry transport failures only; a JSON-RPC error is an answer."""
    from genlayer_py.provider.provider import GenLayerProvider
    original = GenLayerProvider.make_request

    def make_request(self, method, params):
        for i in range(8):
            try:
                return original(self, method, params)
            except Exception as e:
                text = str(e)
                transient = any(s in text for s in (
                    "Connection", "timed out", "SSL", "502", "503", "504",
                    "<!DOCTYPE", "invalid JSON", "RemoteDisconnected", "reset"))
                if not transient or i == 7:
                    raise
                time.sleep(5 + 5 * i)
    GenLayerProvider.make_request = make_request


class Live:
    """A client per account, a transaction recorder, and a few readers."""

    def __init__(self):
        from eth_account import Account
        from genlayer_py import create_client
        from genlayer_py.chains import studionet
        _patch_transport()
        self._create_client = create_client
        self._chain = studionet
        self.client_acct = Account.create()
        self.provider_acct = Account.create()
        self.reader = create_client(chain=studionet, account=Account.create())
        self.record = {"network": "GenLayer StudioNet", "chain_id": studionet.id,
                       "rpc": RPC, "client": self.client_acct.address,
                       "provider": self.provider_acct.address,
                       "started_at": _now(), "transactions": []}
        for acct in (self.client_acct, self.provider_acct):
            rpc("sim_fundAccount", [acct.address, 10 ** 18])
        for acct in (self.client_acct, self.provider_acct):
            self._await(lambda a=acct: self.balance(a.address) > 0, "faucet")

        existing = os.environ.get("AGENTSLA_CONTRACT")
        if existing:
            self.address = existing
            self.record["deployment"] = {"address": existing, "reused": True}
        else:
            code = CONTRACT.read_bytes().replace(b"\r\n", b"\n")
            c = self._client(self.client_acct)
            tx = c.deploy_contract(code=code)
            receipt = self._wait(c, tx, "ACCEPTED")
            self.address = (receipt.get("data") or {}).get("contract_address")
            self.record["deployment"] = {"address": self.address, "tx": _hex(tx),
                                         "decision": receipt.get("result_name")}
        self._await(lambda: self.read("get_protocol_info") is not None, "deployment")
        self.record["contract"] = self.address

    # ── plumbing ──
    def _client(self, acct):
        return self._create_client(chain=self._chain, account=acct)

    def _wait(self, c, tx, status):
        from genlayer_py.types import TransactionStatus
        return c.wait_for_transaction_receipt(
            transaction_hash=tx, status=TransactionStatus[status],
            interval=5000, retries=240)

    @staticmethod
    def _await(predicate, what, tries=40):
        for _ in range(tries):
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(5)
        raise TimeoutError(f"timed out waiting for {what}")

    def balance(self, address) -> int:
        return int(self.reader.get_balance(address))

    def read(self, fn, *args):
        return self.reader.read_contract(address=self.address, function_name=fn,
                                         args=list(args))

    def write(self, acct, fn, *args, value=0, wait="ACCEPTED", step=None):
        c = self._client(acct)
        tx = c.write_contract(address=self.address, function_name=fn,
                              args=list(args), value=value)
        receipt = self._wait(c, tx, wait)
        leader = ((receipt.get("consensus_data") or {}).get("leader_receipt") or [{}])[0]
        result = leader.get("result") or {}
        entry = {
            "step": step or fn,
            "function": fn,
            "caller": "client" if acct is self.client_acct else "provider",
            "tx": _hex(tx),
            "status": receipt.get("status_name"),
            "decision": receipt.get("result_name"),
            "execution": leader.get("execution_result"),
            "reverted": result.get("status") == "rollback",
        }
        if entry["reverted"]:
            entry["refusal"] = str(result.get("payload"))
        self.record["transactions"].append(entry)
        print(f"  {entry['step']:<28} {entry['tx'][:18]}…  {entry['status']} "
              f"{entry['decision']}  {entry['execution']}"
              + (f"  REFUSED: {entry['refusal'][:90]}" if entry["reverted"] else ""))
        return entry

    def save(self):
        self.record["finished_at"] = _now()
        RECORD.write_text(json.dumps(self.record, indent=2, default=str) + "\n",
                          encoding="utf-8")


def _hex(tx):
    return tx.hex() if hasattr(tx, "hex") else str(tx)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@pytest.fixture(scope="session")
def live():
    if not LIVE:
        pytest.skip("set SKIP_INTEGRATION=0 to run against StudioNet")
    harness = Live()
    yield harness
    harness.save()
