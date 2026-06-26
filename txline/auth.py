"""TxLINE authentication — guest JWT, on-chain subscription, API token activation."""
import asyncio
import json
import os
from pathlib import Path

import httpx

BASE_URL = os.getenv("TXLINE_BASE_URL", "https://txline.txodds.com")
DEVNET_BASE_URL = "https://txline-dev.txodds.com"

# Service level 12 = real-time World Cup + International Friendlies (free)
SERVICE_LEVEL_ID = 12
TOKEN_PATH = Path("keys/txline_token.json")


async def get_guest_jwt(base_url: str = BASE_URL) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base_url}/auth/guest/start")
        r.raise_for_status()
        return r.json()["token"]


async def activate_token(jwt: str, tx_sig: str, leagues: list[str] | None = None, base_url: str = BASE_URL) -> str:
    """Sign tx_sig with operator wallet and activate API token.

    Per TxLINE docs: message = f"{tx_sig}:{','.join(leagues)}:{jwt}"
    Body: {txSig, walletSignature, leagues}. leagues=[] for free World Cup tier.
    """
    import base64
    from solders.keypair import Keypair  # type: ignore

    if leagues is None:
        leagues = []  # empty = standard bundle (World Cup free tier)

    keypair_path = os.getenv("OPERATOR_KEYPAIR_PATH", "keys/txline-dev.json")
    with open(keypair_path) as f:
        keypair_data = json.load(f)
    keypair = Keypair.from_bytes(keypair_data)

    # Message format per TxLINE quickstart docs
    message = f"{tx_sig}:{','.join(leagues)}:{jwt}".encode()
    signature = keypair.sign_message(message)
    wallet_sig_b64 = base64.b64encode(bytes(signature)).decode()

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{base_url}/api/token/activate",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "txSig": tx_sig,
                "walletSignature": wallet_sig_b64,
                "leagues": leagues,
            },
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("token") or data  # docs show data.token

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({"apiToken": token, "jwt": jwt}))
    return token


def load_token() -> dict:
    """Load cached JWT + API token from disk."""
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text())
    raise FileNotFoundError("No TxLINE token found. Run scripts/subscribe.py first.")


def auth_headers(token: dict) -> dict:
    # Per TxLINE SSE docs: Authorization carries the guest JWT; X-Api-Token carries the apiToken.
    return {
        "Authorization": f"Bearer {token.get('jwt') or token['apiToken']}",
        "X-Api-Token": token["apiToken"],
    }
