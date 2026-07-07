/**
 * walletconnect_bridge.mjs — wallet-agnostic transaction signing via WalletConnect/Reown
 *
 * Replaces the Phantom-only universal-link flow. Any WalletConnect-compatible
 * Solana wallet (Phantom, Solflare, Backpack, Trust Wallet, etc.) can approve
 * the session and sign — no proprietary per-wallet deep-link scheme.
 *
 * Called by the Python bot via subprocess. Streams JSON lines to stdout:
 *   {"stage":"awaiting_approval","uri":"wc:..."}   — as soon as the pairing URI exists;
 *                                                     the bot shows this to the user immediately.
 *   {"stage":"signed","signed_tx_base64":"...","account":"<pubkey>"}   — on success.
 *   {"stage":"error","reason":"...","message":"..."}                  — on rejection/timeout/failure.
 *
 * Usage:
 *   node scripts/walletconnect_bridge.mjs --tx-base64 <unsigned_tx> --network mainnet|devnet
 *
 * Requires WALLETCONNECT_PROJECT_ID env var (Reown Cloud project ID).
 *
 * NOTE: the Solana CAIP-2 chain IDs below are the commonly documented
 * WalletConnect/Reown identifiers — verify against
 * docs.walletconnect.network/wallet-sdk/chain-support/solana before relying
 * on this in the live demo recording.
 */
import { SignClient } from '@walletconnect/sign-client';
import { parseArgs } from 'util';

// CAIP-2 chain references are capped at 32 chars — Solana's full genesis
// hash (44 chars, base58) must be truncated to its first 32. The untruncated
// form is invalid and gets silently dropped, which is exactly what happened
// here: the session fell back to Bybit's own default (both devnet + mainnet,
// zero methods granted) instead of what we actually requested.
const CHAIN_IDS = {
  mainnet: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp',
  devnet: 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1', // unconfirmed against a live session — verify if devnet is used
};

const APPROVAL_TIMEOUT_MS = 480_000; // 8 min — real human wallet interaction (open app, scan, approve), not an RPC call
const SIGN_TIMEOUT_MS = 180_000;     // 3 min

function out(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function withTimeout(promise, ms, timeoutReason) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(timeoutReason)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

async function main() {
  const { values } = parseArgs({
    args: process.argv.slice(2),
    options: {
      'tx-base64': { type: 'string' },
      network: { type: 'string', default: 'mainnet' },
    },
    strict: false,
  });

  const txBase64 = values['tx-base64'];
  const network = values.network;
  const projectId = process.env.WALLETCONNECT_PROJECT_ID;

  if (!txBase64) {
    out({ stage: 'error', reason: 'missing_args', message: 'Usage: --tx-base64 <unsigned tx> [--network mainnet|devnet]' });
    process.exit(0);
  }
  if (!projectId) {
    out({ stage: 'error', reason: 'missing_project_id', message: 'WALLETCONNECT_PROJECT_ID env var not set.' });
    process.exit(0);
  }
  const chainId = CHAIN_IDS[network];
  if (!chainId) {
    out({ stage: 'error', reason: 'unknown_network', message: `network must be one of: ${Object.keys(CHAIN_IDS).join(', ')}` });
    process.exit(0);
  }

  let client;
  try {
    client = await SignClient.init({
      projectId,
      metadata: {
        name: 'WorldPool',
        description: 'Self-running World Cup prediction pools',
        url: 'https://github.com/mrkanchwala/worldpool',
        icons: [],
      },
    });
  } catch (e) {
    out({ stage: 'error', reason: 'init_failed', message: e.message || String(e) });
    process.exit(0);
  }

  let session;
  try {
    const { uri, approval } = await client.connect({
      requiredNamespaces: {
        solana: {
          methods: ['solana_signTransaction'],
          chains: [chainId],
          events: [],
        },
      },
    });

    out({ stage: 'awaiting_approval', uri });

    session = await withTimeout(approval(), APPROVAL_TIMEOUT_MS, 'wallet_approval_timeout');
  } catch (e) {
    const message = e.message || String(e);
    const reason = message === 'wallet_approval_timeout' ? 'approval_timeout' : 'connect_failed';
    out({ stage: 'error', reason, message });
    process.exit(0);
  }

  // The approved namespace can list multiple chains' accounts (e.g. a wallet
  // offering both devnet and mainnet by default) — pick the one matching what
  // we actually asked for, don't just assume accounts[0] is the right chain.
  const accounts = session.namespaces.solana.accounts;
  const matchedAccount = accounts.find((a) => a.startsWith(`${chainId}:`)) || accounts[0];
  const [, chainRef, account] = matchedAccount.split(':');
  const negotiatedChainId = `solana:${chainRef}`;

  if (negotiatedChainId !== chainId) {
    out({
      stage: 'error',
      reason: 'chain_mismatch',
      message: `Wallet did not approve ${chainId} — only offered: ${accounts.map(a => a.split(':').slice(0,2).join(':')).join(', ')}`,
    });
    process.exit(0);
  }

  try {
    const result = await withTimeout(
      client.request({
        topic: session.topic,
        chainId: negotiatedChainId,
        request: {
          method: 'solana_signTransaction',
          params: { transaction: txBase64 },
        },
      }),
      SIGN_TIMEOUT_MS,
      'wallet_sign_timeout',
    );

    out({ stage: 'signed', signed_tx_base64: result.signature || result.transaction, account });
  } catch (e) {
    const message = e.message || String(e);
    const reason = message === 'wallet_sign_timeout' ? 'sign_timeout' : 'sign_rejected_or_failed';
    out({ stage: 'error', reason, message });
  }
}

main();
