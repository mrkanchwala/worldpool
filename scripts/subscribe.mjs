/**
 * TxLINE subscription script — free World Cup tier (Service Level 12).
 * Run once to get API token saved to keys/txline_token.json
 *
 * Usage: node scripts/subscribe.mjs
 */
import { Connection, Keypair, PublicKey, Transaction, sendAndConfirmTransaction } from "@solana/web3.js";
import { AnchorProvider, Program, setProvider, BN } from "@coral-xyz/anchor";
import { TOKEN_2022_PROGRAM_ID, getAssociatedTokenAddress, createAssociatedTokenAccountInstruction } from "@solana/spl-token";
import axios from "axios";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");

// ── Config ────────────────────────────────────────────────────────────────────
const DEVNET_RPC = "https://api.devnet.solana.com";
const TXLINE_BASE = "https://txline-dev.txodds.com";
const PROGRAM_ID = new PublicKey("6pW64gN1s2uqjHkn1unFeEjAwJkPGHoppGvS715wyP2J");
const TXLINE_MINT = new PublicKey("4Zao8ocPhmMgq7PdsYWyxvqySMGx7xb9cMftPMkEokRG");
const USDT_MINT = new PublicKey("ELWTKspHKCnCfCiCiqYw1EDH77k8VCP74dK9qytG2Ujh");
const SERVICE_LEVEL_ID = 12; // real-time World Cup + Friendlies (free)
const WEEKS = 4;             // minimum 4 weeks

// ── Load keypair ──────────────────────────────────────────────────────────────
const keypairPath = path.join(ROOT, "keys", "txline-dev.json");
const keypairData = JSON.parse(fs.readFileSync(keypairPath, "utf-8"));
const keypair = Keypair.fromSecretKey(new Uint8Array(keypairData));
console.log("Wallet:", keypair.publicKey.toBase58());

const connection = new Connection(DEVNET_RPC, "confirmed");

// ── Step 1: Get guest JWT ─────────────────────────────────────────────────────
async function getGuestJwt() {
  const r = await axios.post(`${TXLINE_BASE}/auth/guest/start`);
  return r.data.token;
}

// ── Step 2: Request devnet USDT via faucet instruction ────────────────────────
async function requestDevnetUsdt(idl) {
  const provider = new AnchorProvider(connection, {
    publicKey: keypair.publicKey,
    signTransaction: async (tx) => { tx.partialSign(keypair); return tx; },
    signAllTransactions: async (txs) => txs.map(tx => { tx.partialSign(keypair); return tx; }),
  });
  setProvider(provider);
  const program = new Program(idl, provider);

  const faucetTrackerSeeds = [Buffer.from("faucet_tracker"), keypair.publicKey.toBuffer()];
  const [faucetTracker] = PublicKey.findProgramAddressSync(faucetTrackerSeeds, PROGRAM_ID);
  const userUsdtAta = await getAssociatedTokenAddress(USDT_MINT, keypair.publicKey, false, TOKEN_2022_PROGRAM_ID);

  try {
    const tx = await program.methods.requestDevnetFaucet().accounts({
      user: keypair.publicKey,
      faucetTracker,
      usdtMint: USDT_MINT,
      userUsdtAta,
      tokenProgram: TOKEN_2022_PROGRAM_ID,
    }).rpc();
    console.log("Faucet USDT received:", tx);
  } catch (e) {
    console.log("Faucet skipped (may already have USDT):", e.message?.slice(0,80));
  }
}

// ── Step 3: Subscribe on-chain ─────────────────────────────────────────────────
async function subscribeOnChain(idl) {
  const provider = new AnchorProvider(connection, {
    publicKey: keypair.publicKey,
    signTransaction: async (tx) => { tx.partialSign(keypair); return tx; },
    signAllTransactions: async (txs) => txs.map(tx => { tx.partialSign(keypair); return tx; }),
  });
  const program = new Program(idl, provider);

  const [pricingMatrix] = PublicKey.findProgramAddressSync([Buffer.from("pricing_matrix")], PROGRAM_ID);
  const [tokenTreasuryPda] = PublicKey.findProgramAddressSync([Buffer.from("token_treasury_v2")], PROGRAM_ID);
  const userTokenAta = await getAssociatedTokenAddress(TXLINE_MINT, keypair.publicKey, false, TOKEN_2022_PROGRAM_ID);

  const tx = await program.methods
    .subscribe(SERVICE_LEVEL_ID, WEEKS)
    .accounts({
      user: keypair.publicKey,
      pricingMatrix,
      tokenMint: TXLINE_MINT,
      userTokenAccount: userTokenAta,
      tokenTreasuryVault: tokenTreasuryPda,
      tokenProgram: TOKEN_2022_PROGRAM_ID,
    })
    .rpc();

  console.log("Subscribe tx:", tx);
  return tx;
}

// ── Step 4: Activate API token ─────────────────────────────────────────────────
async function activateToken(jwt, txSig, leagues = []) {
  // Message format per TxLINE quickstart docs: "{txSig}:{leagues.join(",")}:{jwt}"
  const message = `${txSig}:${leagues.join(",")}:${jwt}`;
  const msgBytes = Buffer.from(message);
  const sigBytes = keypair.sign(msgBytes).signature;
  const walletSignature = Buffer.from(sigBytes).toString("base64");

  const r = await axios.post(
    `${TXLINE_BASE}/api/token/activate`,
    { txSig, walletSignature, leagues },
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  return r.data.token ?? r.data;
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  // Load IDL (fetched separately or hardcoded)
  const idlPath = path.join(ROOT, "keys", "txline-devnet.idl.json");
  if (!fs.existsSync(idlPath)) {
    console.error("IDL not found at", idlPath);
    console.error("Fetch it first: curl https://txline-docs.txodds.com/api-reference/openapi.json > keys/txline-devnet.idl.json");
    process.exit(1);
  }
  const idl = JSON.parse(fs.readFileSync(idlPath, "utf-8"));

  console.log("Step 1: Getting guest JWT...");
  const jwt = await getGuestJwt();
  console.log("JWT:", jwt.slice(0, 30) + "...");

  console.log("Step 2: Requesting devnet USDT...");
  await requestDevnetUsdt(idl);

  console.log("Step 3: Subscribing on-chain (service_level=12, weeks=4)...");
  const txSig = await subscribeOnChain(idl);

  console.log("Step 4: Activating API token...");
  const apiToken = await activateToken(jwt, txSig);
  console.log("API Token:", apiToken.slice(0, 20) + "...");

  // Save to file for Python bot to read
  const tokenData = { apiToken, jwt, txSig };
  fs.writeFileSync(path.join(ROOT, "keys", "txline_token.json"), JSON.stringify(tokenData, null, 2));
  console.log("Token saved to keys/txline_token.json");
}

main().catch(console.error);
