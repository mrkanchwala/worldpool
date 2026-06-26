use {
    anchor_lang::{solana_program::instruction::Instruction, InstructionData, ToAccountMetas},
    litesvm::LiteSVM,
    solana_keypair::Keypair,
    solana_message::{Message, VersionedMessage},
    solana_signer::Signer,
    solana_transaction::versioned::VersionedTransaction,
    anchor_lang::prelude::Pubkey,
};

fn registry_pda() -> Pubkey {
    Pubkey::find_program_address(&[b"worldpool_registry"], &worldpool::ID).0
}

fn settlement_pda(pool_id: &str) -> Pubkey {
    Pubkey::find_program_address(&[b"settlement", pool_id.as_bytes()], &worldpool::ID).0
}

fn setup_svm(payer: &Keypair) -> LiteSVM {
    let mut svm = LiteSVM::new();
    let bytes = include_bytes!("../../../target/deploy/worldpool.so");
    svm.add_program(worldpool::id(), bytes).unwrap();
    svm.airdrop(&payer.pubkey(), 10_000_000_000).unwrap();
    svm
}

fn send_ix(svm: &mut LiteSVM, payer: &Keypair, ix: Instruction) -> litesvm::types::TransactionResult {
    let blockhash = svm.latest_blockhash();
    let msg = Message::new_with_blockhash(&[ix], Some(&payer.pubkey()), &blockhash);
    let tx = VersionedTransaction::try_new(VersionedMessage::Legacy(msg), &[payer]).unwrap();
    svm.send_transaction(tx)
}

#[test]
fn test_initialize_registry() {
    let payer = Keypair::new();
    let mut svm = setup_svm(&payer);

    let ix = Instruction::new_with_bytes(
        worldpool::id(),
        &worldpool::instruction::Initialize {}.data(),
        worldpool::accounts::Initialize {
            operator: payer.pubkey(),
            registry: registry_pda(),
            system_program: anchor_lang::solana_program::system_program::ID,
        }.to_account_metas(None),
    );

    let res = send_ix(&mut svm, &payer, ix);
    assert!(res.is_ok(), "initialize failed: {:?}", res.err());
}

#[test]
fn test_record_settlement() {
    let payer = Keypair::new();
    let mut svm = setup_svm(&payer);
    let pool_id = "pool_001".to_string();

    // Initialize
    let init_ix = Instruction::new_with_bytes(
        worldpool::id(),
        &worldpool::instruction::Initialize {}.data(),
        worldpool::accounts::Initialize {
            operator: payer.pubkey(),
            registry: registry_pda(),
            system_program: anchor_lang::solana_program::system_program::ID,
        }.to_account_metas(None),
    );
    send_ix(&mut svm, &payer, init_ix).unwrap();

    // Record settlement
    let settle_ix = Instruction::new_with_bytes(
        worldpool::id(),
        &worldpool::instruction::RecordSettlement {
            pool_id: pool_id.clone(),
            fixture_id: 12345_i64,
            winning_outcome: "home".to_string(),
            home_score: 2,
            away_score: 1,
            total_payout_usdc_cents: 50_000_000,
            txline_proof_hash: [0u8; 32],
            txline_epoch_day: 1000,
            txline_hour: 18,
        }.data(),
        worldpool::accounts::RecordSettlement {
            operator: payer.pubkey(),
            registry: registry_pda(),
            settlement_record: settlement_pda(&pool_id),
            system_program: anchor_lang::solana_program::system_program::ID,
        }.to_account_metas(None),
    );
    let res = send_ix(&mut svm, &payer, settle_ix);
    assert!(res.is_ok(), "record_settlement failed: {:?}", res.err());
}

#[test]
fn test_invalid_outcome_rejected() {
    let payer = Keypair::new();
    let mut svm = setup_svm(&payer);
    let pool_id = "pool_002".to_string();

    let init_ix = Instruction::new_with_bytes(
        worldpool::id(),
        &worldpool::instruction::Initialize {}.data(),
        worldpool::accounts::Initialize {
            operator: payer.pubkey(),
            registry: registry_pda(),
            system_program: anchor_lang::solana_program::system_program::ID,
        }.to_account_metas(None),
    );
    send_ix(&mut svm, &payer, init_ix).unwrap();

    let bad_ix = Instruction::new_with_bytes(
        worldpool::id(),
        &worldpool::instruction::RecordSettlement {
            pool_id: pool_id.clone(),
            fixture_id: 99999_i64,
            winning_outcome: "invalid".to_string(),  // should fail
            home_score: 0,
            away_score: 0,
            total_payout_usdc_cents: 0,
            txline_proof_hash: [0u8; 32],
            txline_epoch_day: 1000,
            txline_hour: 18,
        }.data(),
        worldpool::accounts::RecordSettlement {
            operator: payer.pubkey(),
            registry: registry_pda(),
            settlement_record: settlement_pda(&pool_id),
            system_program: anchor_lang::solana_program::system_program::ID,
        }.to_account_metas(None),
    );
    let res = send_ix(&mut svm, &payer, bad_ix);
    assert!(res.is_err(), "expected error for invalid outcome");
}
