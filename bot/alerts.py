"""Push alert message formatters — all TG bot push notifications."""
from __future__ import annotations
from txline.parser import ScoreEvent, OddsEvent


def goal_alert(event: ScoreEvent, odds: OddsEvent | None = None, market_volume: float = 0) -> str:
    lines = [
        f"⚽ *GOAL — {event.home_team}*" if event.home_score > event.away_score else f"⚽ *GOAL — {event.away_team}*",
        f"{event.home_team} {event.home_score}–{event.away_score} {event.away_team}",
        f"{'─' * 25}",
    ]
    if odds:
        lines += [
            "📈 *Odds update*",
            f"🏠 {event.home_team}: {odds.home_odds:.2f}",
            f"🤝 Draw: {odds.draw_odds:.2f}",
            f"✈️ {event.away_team}: {odds.away_odds:.2f}",
            f"{'─' * 25}",
        ]
    if market_volume > 0:
        lines.append(f"💰 ${market_volume:,.0f} moved in 60s")
    return "\n".join(lines)


def halftime_alert(event: ScoreEvent, standings: list[dict] | None = None) -> str:
    lines = [
        f"🔔 *Half Time*",
        f"{event.home_team} {event.home_score}–{event.away_score} {event.away_team}",
    ]
    if standings:
        lines.append("─" * 25)
        lines.append("Pool standings:")
        medals = ["🥇", "🥈", "🥉"]
        for i, s in enumerate(standings[:3]):
            medal = medals[i] if i < 3 else "  "
            lines.append(f"{medal} @{s['username']} — {s['outcome']} ${s['amount']:.0f}")
    return "\n".join(lines)


def fulltime_alert(event: ScoreEvent, payouts: list[dict], total_pool: float, losses: list[dict] | None = None) -> str:
    """payouts/losses entries need 'username' attached by the caller (see
    get_users_by_ids) — this function only formats, it doesn't look names up."""
    losses = losses or []
    bettors = len(payouts) + len(losses)
    lines = [
        f"🏆 *FULL TIME — Match Settled*",
        f"{event.home_team} {event.home_score}–{event.away_score} {event.away_team}",
        "─" * 25,
        f"💰 Pool: ${total_pool:.2f} · {bettors} bettors",
        "Anchor settlement confirmed ✅",
        "─" * 25,
    ]
    if payouts or losses:
        if payouts:
            medals = ["🥇", "🥈", "🥉"]
            lines.append("*Winners:*")
            for i, p in enumerate(sorted(payouts, key=lambda p: p["payout"], reverse=True)):
                medal = medals[i] if i < 3 else "•"
                profit_pct = int(((p["payout"] - p["stake"]) / p["stake"]) * 100) if p["stake"] > 0 else 0
                lines.append(f"{medal} @{p.get('username') or '?'} · ${p['stake']:.0f} → *${p['payout']:.2f}* (+{profit_pct}%)")
        if losses:
            lines.append("\n*Lost:*")
            for p in sorted(losses, key=lambda p: p["stake"], reverse=True):
                lines.append(f"🏳️ @{p.get('username') or '?'} · -${p['stake']:.0f}")
    else:
        lines.append("No positions settled.")
    lines.append("\nWinnings sent to your Solana wallet.")
    return "\n".join(lines)


def odds_shift_alert(fixture_label: str, outcome: str, old_odds: float, new_odds: float, shift_pct: float) -> str:
    direction = "↑" if shift_pct > 0 else "↓"
    return (
        f"📊 *Odds shift alert*\n"
        f"{fixture_label}\n\n"
        f"{direction} {outcome}: {old_odds:.2f} → {new_odds:.2f} ({shift_pct:+.0f}%)\n"
        f"Sharp movement detected."
    )


def bet_confirmed(outcome: str, amount: float, odds: float, balance_after: float) -> str:
    potential = amount * odds
    return (
        f"🎯 *Bet placed!*\n\n"
        f"Pick: *{outcome}* @ {odds:.2f} (market ref)\n"
        f"Stake: ${amount:.2f} USDC\n"
        f"Est. return: *${potential:.2f}* _(parimutuel)_\n\n"
        f"Balance: ${balance_after:.2f}\n"
        f"_Final payout depends on total bets placed_"
    )


def deposit_pending(amount: float, deposit_address: str) -> str:
    return (
        f"💳 *Send ${amount:.2f} USDC*\n\n"
        f"To your personal deposit address:\n`{deposit_address}`\n\n"
        f"This address is yours alone — any wallet works, no memo needed\\.\n"
        f"Bot detects the transfer automatically\\.\n\n"
        f"⏳ Watching for your payment \\(10 min window\\)\\."
    )


def deposit_confirmed(amount: float, new_balance: float) -> str:
    return (
        f"✅ *${amount:.2f} USDC deposited\\!*\n\n"
        f"Your balance: *${new_balance:.2f}*\n"
        f"Use /pool to browse open matches\\."
    )
