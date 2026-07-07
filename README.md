# WorldPool ⚽

**Self-running World Cup prediction pools on Telegram. Powered by TxLINE live data. Settled on Solana. Leveraged by Kamino.**

> Built for the [Superteam World Cup Hackathon](https://superteam.fun) · Track: Prediction Markets and Settlement
> Built by **[Quadriga Automations](https://quadrigasolutions.com)** · Murtaza Kanchwala

---

## What is WorldPool?

WorldPool is a Telegram bot that runs USDC prediction pools for every World Cup match. Users deposit once, bet on any open match, and winnings are credited automatically when the final whistle blows — no manual payouts, no admin intervention, no app required.

Live score data comes from **TxLINE** (the official hackathon oracle) via Server-Sent Events. Settlement logic runs on **Solana** via an Anchor program. Users without USDC can borrow it directly inside the bot using **Kamino Finance** — the entire flow from "I don't have funds" to "I have a live bet" happens inside a single Telegram conversation.

---

## How It Works

```
User deposits USDC (or borrows via Kamino)
       ↓
Operator opens a pool for a World Cup match
       ↓
Users browse open pools → pick an outcome (Home / Draw / Away)
       ↓
TxLINE SSE fires full_time event with final score
       ↓
Bot calculates payouts (parimutuel) → credits winners → Anchor settles on-chain
       ↓
Winners get payout notification · Users with open Kamino borrows get repayment reminder
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Telegram Bot                             │
│  Telethon · Python asyncio                                    │
│  /start /pool /deposit /positions /wallet /leverage /settle   │
└───────────────────────┬──────────────────────────────────────┘
                        │ asyncio.gather()
         ┌──────────────┼──────────────┐
         │              │              │
┌────────▼──────┐  ┌────▼────────┐  ┌─▼──────────────────────┐
│  TxLINE SSE   │  │  SQLite     │  │  Kamino Finance         │
│  Score stream │  │  WAL mode   │  │  (Solana mainnet)       │
│  Odds stream  │  │  users      │  │  Node.js subprocess     │
│  Auto-reconnect│  │  pools      │  │  bridge (klend-sdk)     │
└────────┬──────┘  │  positions  │  │  info + borrow modes    │
         │         │  leverage_  │  └─────────────────────────┘
  full_time event  │  positions  │
         │         └─────────────┘
┌────────▼──────┐
│  Anchor Program│
│  Solana devnet │
│  Pool PDA      │
│  USDC escrow   │
└────────────────┘
```

---

## Kamino Leverage — Borrow to Bet

WorldPool integrates **Kamino Finance** — Solana's largest lending protocol — so users can borrow USDC against their existing collateral and use it to place bets, without leaving Telegram.

### How it works

```
1. /wallet  →  register your Solana address
2. /leverage →  bot reads your Kamino position (collateral, LTV, borrow capacity)
3. Pick amount  →  bot generates an unsigned borrow transaction
4. Tap "Open Phantom" link → sign in Phantom (mobile or desktop)
5. Borrowed USDC lands in your wallet
6. /deposit → send it to WorldPool → place your bet
7. After match settles → bot reminds you to repay Kamino
```

### Why composability matters

Kamino holds over **$3B TVL** — most Solana DeFi users already have collateral there. WorldPool is the first prediction market to let those users deploy their idle liquidity directly into World Cup bets without bridging, swapping, or leaving Telegram. The bet is placed; the Kamino loan stays open; winning the bet pays it back.

This composability is a deliberate architectural choice: WorldPool does not custody assets — it acts as an interface layer on top of existing DeFi primitives.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Bot runtime | Python 3.11 · Telethon 1.42 · asyncio |
| Live data | TxLINE SSE (scores + odds, World Cup) |
| Database | SQLite · aiosqlite · WAL mode |
| On-chain settlement | Solana · Anchor · USDC |
| Leverage | Kamino Finance · klend-sdk v9.1.2 · Node.js subprocess |
| HTTP client | httpx (async) |
| Deployment | Hetzner VPS · systemd |

---

## Bot Commands

| Command | Who | Description |
|---------|-----|-------------|
| `/start` | Anyone | Register + welcome card with deposit prompt |
| `/pool` | Anyone | Browse open match pools and place a bet |
| `/positions` | Anyone | View active and settled bets |
| `/deposit` | Anyone | Deposit USDC to WorldPool balance |
| `/wallet <address>` | Anyone | Register Solana wallet for Kamino leverage |
| `/leverage` | Anyone | Borrow USDC via Kamino Finance against your collateral |
| `/leaderboard` | Anyone | Top 10 bettors by profit |
| `/createpool <home> vs <away> <id>` | Operator | Open a prediction pool for a match |
| `/settle <pool_id> <home\|draw\|away>` | Operator | Settle a pool (override / demo) |

**Betting flow:** `/pool` → select match → pick outcome (🟢 Home / ⬜ Draw / 🔴 Away) → choose stake ($5 / $10 / $20 / Custom) → confirm.

**Leverage flow:** `/wallet <addr>` → `/leverage` → pick amount → sign in Phantom → `/deposit` → bet.

---

## TxLINE Integration

WorldPool subscribes to two TxLINE SSE streams simultaneously:

- **Score stream** (`/api/scores/stream`) — fires `goal`, `half_time`, and `full_time` events. `full_time` triggers automatic settlement and repayment reminders.
- **Odds stream** (`/api/odds/stream`) — real-time odds updates. A ≥5% shift fires an alert to all bettors in that pool.

Auth: `Authorization: Bearer <JWT>` + `X-Api-Token: <apiToken>` (both headers required).

Subscription: `scripts/subscribe.mjs` · service_level=12 (mainnet, real World Cup data) · auto-reconnects with exponential backoff (3s → 60s cap). Bot starts normally if no token — all commands still work.

---

## Parimutuel Payouts

Odds shown are **reference only** (from TxLINE market). Actual payouts are parimutuel:

```
winner payout = (your stake / total winning stakes) × total pool
```

This means the pool is self-funding — operator does not take risk. All deposited USDC pays out to winners. The displayed odds communicate market sentiment, not a guaranteed multiplier.

---

## Solana / Anchor

The Anchor program handles USDC escrow per pool:

- **deposit** — moves USDC into the pool PDA vault
- **place_position** — records a bet against the pool's escrow
- **settle_pool** — distributes USDC to winners after `full_time`

If no one bet the winning outcome, stakes are held for operator refund.

Devnet program ID: `6pW64gN1s2uqjHkn1unFeEjAwJkPGHoppGvS715wyP2J`

---

## Deposit Flow

1. `/deposit` → tap amount → bot shows operator wallet + Solana Pay link
2. Send USDC from Phantom with your Telegram user ID as memo
3. Operator confirms on-chain transfer → credits in-bot balance
4. Balance available immediately for betting

Full getTransaction SPL verification is implemented (`_verify_spl_transfer`) — mint, recipient, amount, memo all checked. Auto-credit gated behind `DEPOSIT_AUTOCREDIT=1` (default OFF for safety).

---

## Running Locally

### Prerequisites

- Python 3.11+
- Node.js 18+ (for `scripts/subscribe.mjs` and `scripts/kamino_leverage.mjs`)
- A Telegram bot token (from @BotFather)
- TxLINE API token — run `scripts/subscribe.mjs` once

### Setup

```bash
git clone https://github.com/mrkanchwala/worldpool.git
cd worldpool
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install
cp .env.example .env
# Fill in: TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, ADMIN_TG_ID, OPERATOR_ESCROW_WALLET
```

### TxLINE subscription

```bash
node scripts/subscribe.mjs   # generates keys/txline_token.json
```

### Run

```bash
python3 main.py
```

### Tests

```bash
python3 -m pytest -p no:anchorpy -q   # 32/32
```

### Demo harness (no live match needed)

```bash
# Stop production bot first (frees the bot token)
sudo systemctl stop quadriga-automations-worldpool

python3 demo/demo_match.py

# In Telegram (@txodds_mkbot):
# /start → /demobalance → /createpool Brazil vs Argentina demo01
# /pool → place bet → /playmatch <pool_id> home
# Watch scripted match events + automatic payout in real time
```

---

## VPS Deployment

```bash
# First-time install (needs sudo on VPS — run once)
sudo cp deploy/quadriga-automations-worldpool.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quadriga-automations-worldpool

# Subsequent deploys (rsync + pip + npm ci + restart)
bash scripts/deploy-vps.sh
```

Service: `quadriga-automations-worldpool` (systemd, `Restart=always`, WAL db, PrivateTmp)

### Required environment variables

```
TG_API_ID=
TG_API_HASH=
TG_BOT_TOKEN=
ADMIN_TG_ID=             # required — bot refuses to start without it
OPERATOR_ESCROW_WALLET=  # USDC recipient address
TXLINE_BASE_URL=https://txline.txodds.com
KAMINO_RPC_URL=          # optional — defaults to public mainnet RPC
```

---

## Security

- All SQL parameterized (`?` placeholders) — zero injection surface
- Admin commands fail-closed: bot refuses to start if `ADMIN_TG_ID` is not set
- Kamino subprocess receives allowlisted env only (PATH, HOME, NODE_PATH, KAMINO_RPC_URL) — no TG tokens or keypairs leaked
- Subprocess killed on timeout — no zombie Node processes
- Rate limit: 60s cooldown per user on `/leverage` (prevents RPC DoS)
- Deposit auto-credit requires: correct USDC mint + recipient + amount (±$0.01) + memo contains `tg_user_id` + per-signature dedup
- systemd hardening: `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome=read-only`, `ReadWritePaths` scoped

---

## Hackathon Tracks

| Track | Prize pool | Why WorldPool fits |
|-------|-----------|-------------------|
| Prediction Markets & Settlement | $18,000 | Parimutuel pools, TxLINE oracle, Anchor escrow, auto-settlement on `full_time` |
| Consumer & Fan Experiences | $16,000 | Zero-friction Telegram UX, 104 WC matches, live odds alerts, Kamino leverage, no app required |

---

## Roadmap (post-hackathon)

- [ ] **Phase 2: Trustless settlement** — CPI into TxLINE `validate_stat` so `full_time` scores are verified on-chain; removes operator `/settle` command entirely
- [ ] On-chain deposit auto-detection (full SPL verification, `DEPOSIT_AUTOCREDIT`)
- [ ] Phantom QR code for deposit (in-chat scannable)
- [ ] Leverage position status tracker (`/myloans`)
- [ ] Kamino repayment via Phantom link inside the bot
- [ ] Multi-pool parlays
- [ ] Group chat pools (friends betting together in a shared TG group)
- [ ] Analytics dashboard — pool TVL, user stats, track ROI over the tournament

---

## Built by

**Quadriga Automations** · Murtaza Kanchwala · [quadrigasolutions.com](https://quadrigasolutions.com)

AI automation infrastructure for B2B teams — custom MCP servers, outreach automation, AI-connected CRM, and more.
