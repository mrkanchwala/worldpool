/// WorldPool Settlement Registry
///
/// On-chain audit trail for WorldPool prediction pools.
/// Every settlement is recorded as an immutable PDA with:
/// - pool_id and winning_outcome
/// - TxLINE Merkle proof reference (verifiable against TxLINE's on-chain roots)
/// - payout amounts per winner wallet
///
/// Token transfers happen off-chain (bot → winner wallets).
/// This contract provides the cryptographic proof that settlement was legitimate.
use anchor_lang::prelude::*;

declare_id!("8HcYBkhvfiRJXwr3fGEDDeU6Z8tYsVr46x44wKmfspWL");

#[program]
pub mod worldpool {
    use super::*;

    /// Initialize the operator registry. Called once by the operator.
    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        let registry = &mut ctx.accounts.registry;
        registry.operator = ctx.accounts.operator.key();
        registry.pool_count = 0;
        registry.total_settled_usdc = 0;
        emit!(RegistryInitialized { operator: registry.operator });
        Ok(())
    }

    /// Record a pool settlement on-chain.
    /// Stores the winning outcome and TxLINE Merkle proof reference.
    /// Anyone can independently verify the proof against TxLINE's on-chain roots.
    pub fn record_settlement(
        ctx: Context<RecordSettlement>,
        pool_id: String,
        fixture_id: i64,
        winning_outcome: String,  // "home" | "away" | "draw"
        home_score: u8,
        away_score: u8,
        total_payout_usdc_cents: u64,  // USDC in cents (6 decimal precision)
        txline_proof_hash: [u8; 32],   // Merkle proof root hash from TxLINE
        txline_epoch_day: u16,
        txline_hour: u8,
    ) -> Result<()> {
        require!(pool_id.len() <= 16, WorldPoolError::PoolIdTooLong);
        require!(winning_outcome.len() <= 8, WorldPoolError::InvalidOutcome);
        require!(
            winning_outcome == "home" || winning_outcome == "away" || winning_outcome == "draw",
            WorldPoolError::InvalidOutcome,
        );

        let record = &mut ctx.accounts.settlement_record;
        record.pool_id = pool_id.clone();
        record.fixture_id = fixture_id;
        record.winning_outcome = winning_outcome.clone();
        record.home_score = home_score;
        record.away_score = away_score;
        record.total_payout_usdc_cents = total_payout_usdc_cents;
        record.txline_proof_hash = txline_proof_hash;
        record.txline_epoch_day = txline_epoch_day;
        record.txline_hour = txline_hour;
        record.operator = ctx.accounts.operator.key();
        record.settled_at = Clock::get()?.unix_timestamp;

        let registry = &mut ctx.accounts.registry;
        registry.pool_count = registry.pool_count.checked_add(1).ok_or(WorldPoolError::Overflow)?;
        registry.total_settled_usdc = registry.total_settled_usdc
            .checked_add(total_payout_usdc_cents)
            .ok_or(WorldPoolError::Overflow)?;

        emit!(PoolSettled {
            pool_id,
            fixture_id,
            winning_outcome,
            home_score,
            away_score,
            total_payout_usdc_cents,
        });
        Ok(())
    }

    /// Record individual payout to a winner.
    /// Creates an on-chain proof that winner received their payout.
    pub fn record_payout(
        ctx: Context<RecordPayout>,
        pool_id: String,
        amount_usdc_cents: u64,
        tx_signature: [u8; 64],  // USDC transfer signature for verification
    ) -> Result<()> {
        let payout = &mut ctx.accounts.payout_record;
        payout.pool_id = pool_id.clone();
        payout.winner = ctx.accounts.winner.key();
        payout.amount_usdc_cents = amount_usdc_cents;
        payout.tx_signature = tx_signature;
        payout.recorded_at = Clock::get()?.unix_timestamp;

        emit!(PayoutRecorded {
            pool_id,
            winner: ctx.accounts.winner.key(),
            amount_usdc_cents,
        });
        Ok(())
    }
}

// ── Accounts ──────────────────────────────────────────────────────────────────

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub operator: Signer<'info>,

    #[account(
        init,
        payer = operator,
        space = 8 + OperatorRegistry::INIT_SPACE,
        seeds = [b"worldpool_registry"],
        bump,
    )]
    pub registry: Account<'info, OperatorRegistry>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(pool_id: String)]
pub struct RecordSettlement<'info> {
    #[account(mut)]
    pub operator: Signer<'info>,

    #[account(mut, seeds = [b"worldpool_registry"], bump, has_one = operator)]
    pub registry: Account<'info, OperatorRegistry>,

    #[account(
        init,
        payer = operator,
        space = 8 + SettlementRecord::INIT_SPACE,
        seeds = [b"settlement", pool_id.as_bytes()],
        bump,
    )]
    pub settlement_record: Account<'info, SettlementRecord>,

    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(pool_id: String)]
pub struct RecordPayout<'info> {
    #[account(mut)]
    pub operator: Signer<'info>,

    #[account(seeds = [b"worldpool_registry"], bump, has_one = operator)]
    pub registry: Account<'info, OperatorRegistry>,

    /// CHECK: winner wallet — identity only, no token ops
    pub winner: UncheckedAccount<'info>,

    #[account(
        init,
        payer = operator,
        space = 8 + PayoutRecord::INIT_SPACE,
        seeds = [b"payout", pool_id.as_bytes(), winner.key().as_ref()],
        bump,
    )]
    pub payout_record: Account<'info, PayoutRecord>,

    pub system_program: Program<'info, System>,
}

// ── State ─────────────────────────────────────────────────────────────────────

#[account]
#[derive(InitSpace)]
pub struct OperatorRegistry {
    pub operator: Pubkey,
    pub pool_count: u32,
    pub total_settled_usdc: u64,
}

#[account]
#[derive(InitSpace)]
pub struct SettlementRecord {
    #[max_len(16)]
    pub pool_id: String,
    pub fixture_id: i64,
    #[max_len(8)]
    pub winning_outcome: String,
    pub home_score: u8,
    pub away_score: u8,
    pub total_payout_usdc_cents: u64,
    pub txline_proof_hash: [u8; 32],
    pub txline_epoch_day: u16,
    pub txline_hour: u8,
    pub operator: Pubkey,
    pub settled_at: i64,
}

#[account]
#[derive(InitSpace)]
pub struct PayoutRecord {
    #[max_len(16)]
    pub pool_id: String,
    pub winner: Pubkey,
    pub amount_usdc_cents: u64,
    pub tx_signature: [u8; 64],
    pub recorded_at: i64,
}

// ── Events ────────────────────────────────────────────────────────────────────

#[event]
pub struct RegistryInitialized { pub operator: Pubkey }

#[event]
pub struct PoolSettled {
    pub pool_id: String,
    pub fixture_id: i64,
    pub winning_outcome: String,
    pub home_score: u8,
    pub away_score: u8,
    pub total_payout_usdc_cents: u64,
}

#[event]
pub struct PayoutRecorded {
    pub pool_id: String,
    pub winner: Pubkey,
    pub amount_usdc_cents: u64,
}

// ── Errors ────────────────────────────────────────────────────────────────────

#[error_code]
pub enum WorldPoolError {
    #[msg("Pool ID exceeds 16 characters")]
    PoolIdTooLong,
    #[msg("Outcome must be home, away, or draw")]
    InvalidOutcome,
    #[msg("Arithmetic overflow")]
    Overflow,
}
