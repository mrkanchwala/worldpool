"""Wallet-agnostic transaction signing via WalletConnect/Reown.

Replaces the Phantom-only universal-link flow in leverage.py. Any
WalletConnect-compatible Solana wallet can approve the session and sign — no
per-wallet proprietary deep-link scheme. Bridges to scripts/walletconnect_bridge.mjs
(Node), which streams JSON-lines: the pairing URI as soon as it exists, then
the final signed result (or error) once the wallet responds.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

_WC_BRIDGE_MJS = Path(__file__).resolve().parents[1] / "scripts" / "walletconnect_bridge.mjs"
_NODE_BIN = os.environ.get("NODE_BIN", "node")
_APPROVAL_AND_SIGN_TIMEOUT = 320  # comfortably longer than the mjs script's own 180s+120s internal timeouts


async def sign_via_walletconnect(
    tx_base64: str,
    network: str,
    on_uri_ready: Callable[[str], Awaitable[None]],
) -> dict:
    """Runs the WalletConnect bridge, invoking on_uri_ready(uri) as soon as the
    pairing link exists so the caller can show it to the user immediately.
    Returns the bridge's final {"stage": "signed"|"error", ...} dict."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", ""),
        "NODE_PATH": os.environ.get("NODE_PATH", ""),
        "WALLETCONNECT_PROJECT_ID": os.environ.get("WALLETCONNECT_PROJECT_ID", ""),
    }

    proc = await asyncio.create_subprocess_exec(
        _NODE_BIN, str(_WC_BRIDGE_MJS), "--tx-base64", tx_base64, "--network", network,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )

    result = {"stage": "error", "reason": "no_output", "message": "WalletConnect bridge produced no output."}
    try:
        uri_sent = False
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=_APPROVAL_AND_SIGN_TIMEOUT)
            if not line:
                break
            try:
                data = json.loads(line.decode().strip())
            except json.JSONDecodeError:
                continue
            stage = data.get("stage")
            if stage == "awaiting_approval" and not uri_sent:
                uri_sent = True
                await on_uri_ready(data["uri"])
            elif stage in ("signed", "error"):
                result = data
                break
    except asyncio.TimeoutError:
        result = {"stage": "error", "reason": "timeout", "message": "No response from wallet bridge in time."}
    except Exception as e:
        logger.exception("WalletConnect bridge call failed")
        result = {"stage": "error", "reason": "bridge_error", "message": str(e)}
    finally:
        if proc.returncode is None:
            proc.kill()
            try:
                await proc.communicate()
            except ProcessLookupError:
                pass

    return result


async def broadcast_signed_transaction(rpc_url: str, signed_tx_base64: str) -> str:
    """Submit a fully-signed transaction (received back from the wallet via
    WalletConnect) to the network."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(rpc_url, json={
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [signed_tx_base64, {"encoding": "base64"}],
        })
    result = r.json()
    if "error" in result:
        raise RuntimeError(f"sendTransaction failed: {result['error']}")
    return result["result"]
