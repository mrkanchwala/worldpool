"""Push alert message formatters — all TG bot push notifications."""
from __future__ import annotations
from txline.parser import ScoreEvent, OddsEvent


def goal_alert(event: ScoreEvent, odds: OddsEvent | None = None, market_volume: float = 0) -> str:
    lines = [
        f"⚽ *GOAL — {event.home_team}*" if event.home_score > event.away_score else f"⚽ *GOAL — {event.away_team}*",
        f"{event.home_team} {event.home_score}–{event.away_score} {event.away_team}",
        f"{'~' * 25}",
    ]
    if odds:
        lines += [
            "📈 *Odds update*",
            f"🏠 {event.home_team}: {odds.home_odds:.2f}",
            f"🤝 Draw: {odds.draw_odds:.2f}",
            f"✈️ {event.away_team}: {odds.away_odds:.2f}",
            f"{'~' * 25}",
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
        lines.append("~" * 25)
        lines.append("Pool standings:")
        medals = ["🥇", "🥈", "🥉"]
        for i, s in enumerate(standings[:3]):
            medal = medals[i] if i < 3 else "  "
            lines.append(f"{medal} @{s['username']} — {s['outcome']} ${s['amount']:.0f}")
    return "\n".join(lines)


def fulltime_alert(event: ScoreEvent, payouts: list[dict], total_pool: float) -> str:
    lines = [
        f"🏆 *FULL TIME — Match Settled*",
        f"{event.home_team} {event.home_score}–{event.away_score} {event.away_team}",
        "~" * 25,
        "Anchor settlement confirmed ✅",
        "~" * 25,
    ]
    if payouts:
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(payouts[:3]):
            medal = medals[i] if i < 3 else "  "
            profit_pct = int(((p["payout"] - p["stake"]) / p["stake"]) * 100) if p["stake"] > 0 else 0
            sign = "+" if profit_pct >= 0 else ""
            lines.append(f"{medal} @{p.get('username','?')} · ${p['stake']:.0f} → *${p['payout']:.2f}* ({sign}{profit_pct}%)")
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


def deposit_pending(amount: float, pay_url: str, memo: str, operator_wallet: str) -> str:
    return (
        f"💳 *Deposit ${amount:.2f} USDC*\n\n"
        f"Tap the button below to pay in Phantom \\(amount \\+ memo pre\\-filled\\)\\.\n\n"
        f"*Or send manually:*\n"
        f"To: `{operator_wallet}`\n"
        f"Amount: `{amount:.2f}` USDC\n"
        f"Memo: `{memo}` ← required\n\n"
        f"⏳ Operator confirms on\\-chain \\(usually a few minutes\\)\\."
    )


def deposit_confirmed(amount: float, new_balance: float) -> str:
    return (
        f"✅ *${amount:.2f} USDC deposited\\!*\n\n"
        f"Your balance: *${new_balance:.2f}*\n"
        f"Use /pool to browse open matches\\."
    )
