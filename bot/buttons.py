"""Inline keyboard builders for all TG bot flows."""
from __future__ import annotations
from telethon import Button


def main_menu() -> list:
    return [
        [Button.inline("💰 Deposit USDC", b"deposit"), Button.inline("🏟️ Browse Matches", b"browse")],
        [Button.inline("📊 My Positions", b"positions"), Button.inline("🏆 Leaderboard", b"leaderboard")],
        [Button.inline("⚡ Leverage", b"leverage"), Button.inline("ℹ️ How it works", b"howto")],
    ]


def deposit_amounts() -> list:
    return [
        [Button.inline("$10 USDC", b"dep_10"), Button.inline("$25 USDC", b"dep_25")],
        [Button.inline("$50 USDC", b"dep_50"), Button.inline("✏️ Custom", b"dep_custom")],
    ]


def pay_button(pay_url: str) -> list:
    """URL button that opens the Solana Pay URI — renders as a tappable button on mobile."""
    return [[Button.url("💳 Pay in Phantom", pay_url)]]


def pool_list(pools: list) -> list:
    """Build pool selection buttons from pool rows."""
    buttons = []
    for p in pools[:8]:
        label = f"{'🔴 ' if p['status'] == 'open' else ''}{p['home_team']} vs {p['away_team']}"
        data = f"pool_{p['pool_id']}".encode()
        buttons.append([Button.inline(label, data)])
    buttons.append([Button.inline("➕ Create Pool", b"create_pool")])
    return buttons


def outcome_buttons(pool_id: str, home_team: str, away_team: str,
                    home_odds: float, draw_odds: float, away_odds: float) -> list:
    pid = pool_id.encode()
    return [
        [
            Button.inline(f"🏠 {home_team}\n{home_odds:.2f}", f"bet_{pool_id}_home".encode()),
            Button.inline(f"🤝 Draw\n{draw_odds:.2f}", f"bet_{pool_id}_draw".encode()),
            Button.inline(f"✈️ {away_team}\n{away_odds:.2f}", f"bet_{pool_id}_away".encode()),
        ]
    ]


def stake_buttons(pool_id: str, outcome: str) -> list:
    base = f"stake_{pool_id}_{outcome}"
    return [
        [Button.inline("$5", f"{base}_5".encode()), Button.inline("$10", f"{base}_10".encode())],
        [Button.inline("$20", f"{base}_20".encode()), Button.inline("✏️ Custom", f"{base}_custom".encode())],
    ]


def confirm_buttons(pool_id: str, outcome: str, amount: float) -> list:
    return [
        [
            Button.inline("✅ Confirm", f"confirm_{pool_id}_{outcome}_{amount}".encode()),
            Button.inline("❌ Cancel", b"cancel"),
        ]
    ]


def positions_menu() -> list:
    return [
        [Button.inline("🔄 Refresh", b"positions"), Button.inline("🏆 Leaderboard", b"leaderboard")],
        [Button.inline("🏟️ More Matches", b"browse"), Button.inline("💸 Withdraw", b"withdraw")],
    ]
