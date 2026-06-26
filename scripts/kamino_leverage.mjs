/**
 * kamino_leverage.mjs — Kamino Lend borrow helper for WorldPool
 *
 * Called by the Python bot via subprocess. Outputs JSON to stdout.
 *
 * Usage:
 *   node scripts/kamino_leverage.mjs --mode info   --wallet <base58>
 *   node scripts/kamino_leverage.mjs --mode borrow --wallet <base58> --amount <usdc_float>
 *
 * Info mode output:
 *   { ok: true, collateral_usd, borrowed_usd, available_usd, health_factor, borrow_apy, obligation }
 *   { ok: false, reason: "no_obligation" | "no_collateral" | "error", message }
 *
 * Borrow mode output:
 *   { ok: true, tx_base64, phantom_url, amount_usdc, estimated_apy }
 *   { ok: false, reason, message }
 */

import {
  KaminoMarket,
  KaminoAction,
  VanillaObligation,
  PROGRAM_ID,
  getMedianSlotDurationInMsFromLastEpochs,
} from '@kamino-finance/klend-sdk';
import {
  createDefaultRpcTransport,
  createRpc,
  createSolanaRpcApi,
  DEFAULT_RPC_CONFIG,
  address,
} from '@solana/kit';
import BN from 'bn.js';
import { parseArgs } from 'util';

// ── Constants ──────────────────────────────────────────────────────────────────

const MAIN_MARKET   = address('7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF');
const USDC_MINT     = address('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v');
const USDC_DECIMALS = 6;
const RPC_URL       = process.env.KAMINO_RPC_URL || 'https://api.mainnet-beta.solana.com';
const CLUSTER       = process.env.KAMINO_CLUSTER  || 'mainnet-beta';

// ── Helpers ────────────────────────────────────────────────────────────────────

function out(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

function fail(reason, message) {
  out({ ok: false, reason, message });
  process.exit(0);
}

function phantomUrl(txBase64) {
  const encoded = encodeURIComponent(txBase64);
  return `https://phantom.app/ul/v1/signAndSendTransaction?transaction=${encoded}&cluster=${CLUSTER}`;
}

async function loadMarket(rpc) {
  const slotDuration = await getMedianSlotDurationInMsFromLastEpochs();
  const market = await KaminoMarket.load(rpc, MAIN_MARKET, slotDuration);
  if (!market) throw new Error('Failed to load Kamino market');
  return market;
}

// ── Info mode ──────────────────────────────────────────────────────────────────

async function infoMode(rpc, walletAddr) {
  const market = await loadMarket(rpc);
  const wallet = address(walletAddr);

  // Try vanilla obligation first (most common for regular users)
  let obligation = await market.getUserVanillaObligation(wallet);

  // Fallback: scan all user obligations
  if (!obligation) {
    const all = await market.getAllUserObligations(wallet);
    obligation = all.length > 0 ? all[0] : null;
  }

  if (!obligation) {
    return out({
      ok: false,
      reason: 'no_obligation',
      message: 'No Kamino lending position found for this wallet. Deposit SOL or USDC on app.kamino.finance first.',
    });
  }

  const stats = obligation.refreshedStats;
  const collateral = parseFloat(stats.userTotalCollateralDeposit.toFixed(2));
  const borrowed   = parseFloat(stats.userTotalBorrow.toFixed(2));
  const borrowLimit = parseFloat(stats.borrowLimit.toFixed(2));
  const available  = Math.max(0, parseFloat((borrowLimit - borrowed).toFixed(2)));
  const ltv        = obligation.loanToValue ? parseFloat((obligation.loanToValue().toNumber() * 100).toFixed(1)) : null;

  // USDC borrow APY
  let borrowApy = null;
  try {
    const slot = await rpc.getSlot().send();
    const reserves = market.getReservesByMint(USDC_MINT);
    if (reserves && reserves.length > 0) {
      borrowApy = parseFloat((reserves[0].totalBorrowAPY(slot) * 100).toFixed(2));
    }
  } catch (_) {}

  out({
    ok: true,
    collateral_usd: collateral,
    borrowed_usd: borrowed,
    borrow_limit_usd: borrowLimit,
    available_usd: available,
    ltv_pct: ltv,
    borrow_apy_pct: borrowApy,
    obligation: obligation.obligationAddress?.toString() || null,
  });
}

// ── Borrow mode ────────────────────────────────────────────────────────────────

async function borrowMode(rpc, walletAddr, amountUsdc) {
  if (isNaN(amountUsdc) || amountUsdc <= 0) {
    return fail('invalid_amount', 'Amount must be a positive number');
  }

  const market = await loadMarket(rpc);
  const wallet = address(walletAddr);

  // Verify user has collateral
  let obligation = await market.getUserVanillaObligation(wallet);
  if (!obligation) {
    const all = await market.getAllUserObligations(wallet);
    obligation = all.length > 0 ? all[0] : null;
  }
  if (!obligation) {
    return fail('no_obligation', 'No Kamino position. Deposit collateral on app.kamino.finance first.');
  }

  const stats    = obligation.refreshedStats;
  const borrowed = parseFloat(stats.userTotalBorrow.toFixed(2));
  const limit    = parseFloat(stats.borrowLimit.toFixed(2));
  const available = Math.max(0, limit - borrowed);

  if (amountUsdc > available) {
    return fail(
      'exceeds_limit',
      `Requested $${amountUsdc.toFixed(2)} exceeds available borrow capacity $${available.toFixed(2)}.`
    );
  }

  // Amount in lamports (USDC 6 decimals)
  const amountLamports = new BN(Math.floor(amountUsdc * 10 ** USDC_DECIMALS));

  // Mock signer — we only need the address to build the unsigned tx
  const mockSigner = {
    address: wallet,
    signTransactions: async (txs) => txs,
  };

  // Build borrow transaction
  const borrowAction = await KaminoAction.buildBorrowTxns(
    market,
    amountLamports,
    USDC_MINT,
    mockSigner,
    new VanillaObligation(PROGRAM_ID),
    true,   // requestElevationGroup
    undefined
  );

  // Serialize all instructions into a base64 transaction
  const ixs = KaminoAction.actionToIxs(borrowAction);
  const allIxs = [...ixs.setupIxs, ...ixs.inIxs, ...ixs.inPostIxs, ...ixs.cleanupIxs].filter(Boolean);

  // Convert @solana/kit instructions to web3.js v1 TransactionInstruction format
  // then build a VersionedTransaction for Phantom signing
  const { Connection, PublicKey, TransactionInstruction, VersionedTransaction, TransactionMessage } =
    await import('@solana/web3.js');

  const connection = new Connection(RPC_URL);
  const { blockhash } = await connection.getLatestBlockhash('finalized');

  const legacyIxs = allIxs.map((ix) => {
    const programId = new PublicKey(ix.programAddress.toString());
    const keys = ix.accounts.map((acc) => ({
      pubkey: new PublicKey(acc.address.toString()),
      isSigner: acc.role === 2 || acc.role === 3,  // writable signer or readonly signer
      isWritable: acc.role === 1 || acc.role === 3, // writable
    }));
    return new TransactionInstruction({
      programId,
      keys,
      data: Buffer.from(ix.data),
    });
  });

  const feePayer = new PublicKey(walletAddr);
  const message = new TransactionMessage({
    payerKey: feePayer,
    recentBlockhash: blockhash,
    instructions: legacyIxs,
  }).compileToV0Message();

  const tx = new VersionedTransaction(message);
  const txBase64 = Buffer.from(tx.serialize()).toString('base64');

  // USDC borrow APY
  let borrowApy = null;
  try {
    const slot = await rpc.getSlot().send();
    const reserves = market.getReservesByMint(USDC_MINT);
    if (reserves && reserves.length > 0) {
      borrowApy = parseFloat((reserves[0].totalBorrowAPY(slot) * 100).toFixed(2));
    }
  } catch (_) {}

  out({
    ok: true,
    amount_usdc: amountUsdc,
    tx_base64: txBase64,
    phantom_url: phantomUrl(txBase64),
    estimated_apy_pct: borrowApy,
    note: 'Transaction unsigned — user must sign in Phantom. Borrowed USDC will arrive in wallet; send to WorldPool operator to credit balance.',
  });
}

// ── Entry point ────────────────────────────────────────────────────────────────

const { values } = parseArgs({
  args: process.argv.slice(2),
  options: {
    mode:   { type: 'string' },
    wallet: { type: 'string' },
    amount: { type: 'string' },
  },
  strict: false,
});

const mode   = values.mode;
const wallet = values.wallet;
const amount = parseFloat(values.amount || '0');

if (!mode || !wallet) {
  fail('missing_args', 'Usage: --mode info|borrow --wallet <address> [--amount <usdc>]');
}

const api = createSolanaRpcApi({ ...DEFAULT_RPC_CONFIG, defaultCommitment: 'processed' });
const rpc = createRpc({ api, transport: createDefaultRpcTransport({ url: RPC_URL }) });

try {
  if (mode === 'info') {
    await infoMode(rpc, wallet);
  } else if (mode === 'borrow') {
    await borrowMode(rpc, wallet, amount);
  } else {
    fail('unknown_mode', `Unknown mode: ${mode}. Use 'info' or 'borrow'.`);
  }
} catch (e) {
  fail('error', e.message || String(e));
}
