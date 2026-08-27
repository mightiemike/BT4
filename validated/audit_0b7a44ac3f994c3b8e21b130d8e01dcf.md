Based on my analysis of `runtime/src/bank/check_transactions.rs`, this question does not describe a valid vulnerability in this codebase.

**Key findings:**

`load_message_nonce_data` is a purely read-only helper that loads a nonce account and verifies it against the message's recent blockhash — it does not itself advance or mutate any nonce state [1](#0-0) . Its caller, `check_nonce_transaction_validity`, is also just an advisory check used during transaction age/eligibility checking (for forwarding decisions and computing `CheckedTransactionDetails`), not the authoritative nonce-advancement path [2](#0-1) .

The actual authoritative nonce validation and advancement happens independently in `svm/src/transaction_processor.rs::validate_transaction_nonce`, which re-loads the nonce account, re-verifies it via `verify_nonce_account`, and critically requires **both** that the nonce can be advanced (`nonce_can_be_advanced`) **and** that the transaction is properly signed by the nonce's stored authority (`nonce_authority_is_valid`) before mutating and advancing the nonce state [3](#0-2) . If the attacker pairs the transaction with a nonce account they don't control the authority for, this check fails and the transaction is rejected with `BlockhashNotFound` — it never reaches execution, so no fee is charged and no nonce is touched.

Additionally, even for transactions that fail during execution (e.g., `InstructionError`), the runtime intentionally still advances the nonce and commits the `RollbackAccounts` to prevent exactly the kind of replay described in the question [4](#0-3) , and this rollback-driven advancement is verified by existing tests such as `test_nonce_authority` (which confirms fee is *not* charged and nonce does *not* advance when the authority check fails) and the instruction-error nonce-advancement test [5](#0-4) , and [6](#0-5) . Since the double gate (age-check helper + authoritative SVM-level `validate_transaction_nonce`) enforces authority checks before any advancement or execution, `load_message_nonce_data` being non-authoritative does not create a path for nonce reuse or replay.

### No Vulnerability found for this question.

### Citations

**File:** runtime/src/bank/check_transactions.rs (L260-286)
```rust
    pub(super) fn check_nonce_transaction_validity(
        &self,
        message: &impl SVMMessage,
        next_durable_nonce: &DurableNonce,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> Option<(Pubkey, u64)> {
        let nonce_is_advanceable = message.recent_blockhash() != next_durable_nonce.as_hash();
        if !nonce_is_advanceable {
            return None;
        }

        let (nonce_address, nonce_data) =
            self.load_message_nonce_data(message, strict_nonce_size_check)?;

        if strict_nonce_authority_check
            && !message
                .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
                .any(|signer| signer == &nonce_data.authority)
        {
            return None;
        }

        let previous_lamports_per_signature = nonce_data.get_lamports_per_signature();

        Some((nonce_address, previous_lamports_per_signature))
    }
```

**File:** runtime/src/bank/check_transactions.rs (L288-302)
```rust
    pub(super) fn load_message_nonce_data(
        &self,
        message: &impl SVMMessage,
        strict_nonce_size_check: bool,
    ) -> Option<(Pubkey, NonceData)> {
        let nonce_address = message.get_durable_nonce()?;
        let nonce_account = self.get_account_with_fixed_root(nonce_address)?;
        if strict_nonce_size_check && nonce_account.data().len() != NonceState::size() {
            return None;
        }
        let nonce_data =
            nonce_account::verify_nonce_account(&nonce_account, message.recent_blockhash())?;

        Some((*nonce_address, nonce_data))
    }
```

**File:** svm/src/transaction_processor.rs (L892-922)
```rust
        // This function verifies:
        // * Nonce account owner is SystemProgram
        // * Nonce account parses as State::Initialized
        // * Stored durable nonce matches the message blockhash
        let Some(nonce_data) = verify_nonce_account(&nonce_account, message.recent_blockhash())
        else {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        };

        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);

        if nonce_can_be_advanced && nonce_authority_is_valid {
            let next_nonce_state = NonceState::new_initialized(
                &nonce_data.authority,
                *next_durable_nonce,
                next_lamports_per_signature,
            );
            nonce_account
                .set_state(&NonceVersions::new(next_nonce_state))
                .expect("Serializing into a validated nonce account cannot fail");

            Ok(NonceInfo::new(*nonce_address, nonce_account))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
```

**File:** svm/src/nonce_info.rs (L35-56)
```rust
    // Advance the stored blockhash to prevent fee theft by someone
    // replaying nonce transactions that have failed with an
    // `InstructionError`.
    #[cfg(feature = "dev-context-only-utils")]
    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn try_advance_nonce(
        &mut self,
        durable_nonce: DurableNonce,
        lamports_per_signature: u64,
    ) -> Result<(), AdvanceNonceError> {
        let nonce_versions = StateMut::<NonceVersions>::state(&self.account)
            .map_err(|_| AdvanceNonceError::Invalid)?;
        if let NonceState::Initialized(data) = nonce_versions.state() {
            let nonce_state =
                NonceState::new_initialized(&data.authority, durable_nonce, lamports_per_signature);
            let nonce_versions = NonceVersions::new(nonce_state);
            self.account.set_state(&nonce_versions).unwrap();
            Ok(())
        } else {
            Err(AdvanceNonceError::Uninitialized)
        }
    }
```

**File:** runtime/src/bank/tests.rs (L4404-4429)
```rust
    assert_eq!(
        bank.process_transaction(&nonce_tx),
        Err(TransactionError::InstructionError(
            1,
            solana_system_interface::error::SystemError::ResultWithNegativeLamports.into(),
        ))
    );
    /* Check fee charged and nonce has advanced */
    let mut recent_message = nonce_tx.message.clone();
    recent_message.recent_blockhash = bank.last_blockhash();
    expected_balance -= bank
        .get_fee_for_message(&new_sanitized_message(recent_message))
        .unwrap();
    assert_eq!(bank.get_balance(&custodian_pubkey), expected_balance);
    assert_ne!(
        nonce_hash,
        get_nonce_blockhash(&bank, &nonce_pubkey).unwrap()
    );
    /* Confirm replaying a TX that failed with InstructionError::* now
     * fails with TransactionError::BlockhashNotFound
     */
    assert_eq!(
        bank.process_transaction(&nonce_tx),
        Err(TransactionError::BlockhashNotFound),
    );
}
```

**File:** runtime/src/bank/tests.rs (L4431-4481)
```rust
#[test]
fn test_nonce_authority() {
    agave_logger::setup();
    let (mut bank, _mint_keypair, custodian_keypair, nonce_keypair, bank_forks) =
        setup_nonce_with_bank(10_000_000, |_| {}, 5_000_000, 250_000, None).unwrap();
    let alice_keypair = Keypair::new();
    let alice_pubkey = alice_keypair.pubkey();
    let custodian_pubkey = custodian_keypair.pubkey();
    let nonce_pubkey = nonce_keypair.pubkey();
    let bad_nonce_authority_keypair = Keypair::new();
    let bad_nonce_authority = bad_nonce_authority_keypair.pubkey();
    let custodian_account = bank.get_account(&custodian_pubkey).unwrap();

    debug!("alice: {alice_pubkey}");
    debug!("custodian: {custodian_pubkey}");
    debug!("nonce: {nonce_pubkey}");
    debug!("nonce account: {:?}", bank.get_account(&nonce_pubkey));
    debug!("cust: {custodian_account:?}");
    let nonce_hash = get_nonce_blockhash(&bank, &nonce_pubkey).unwrap();

    for _ in 0..MAX_RECENT_BLOCKHASHES + 1 {
        goto_end_of_slot(bank.clone());
        bank = new_from_parent_with_fork_next_slot(bank, bank_forks.as_ref());
    }

    let nonce_tx = Transaction::new_signed_with_payer(
        &[
            system_instruction::advance_nonce_account(&nonce_pubkey, &bad_nonce_authority),
            system_instruction::transfer(&custodian_pubkey, &alice_pubkey, 42),
        ],
        Some(&custodian_pubkey),
        &[&custodian_keypair, &bad_nonce_authority_keypair],
        nonce_hash,
    );
    debug!("{nonce_tx:?}");
    let initial_custodian_balance = custodian_account.lamports();
    assert_eq!(
        bank.process_transaction(&nonce_tx),
        Err(TransactionError::BlockhashNotFound),
    );
    /* Check fee was *not* charged and nonce has *not* advanced */
    let mut recent_message = nonce_tx.message;
    recent_message.recent_blockhash = bank.last_blockhash();
    assert_eq!(
        bank.get_balance(&custodian_pubkey),
        initial_custodian_balance
    );
    assert_eq!(
        nonce_hash,
        get_nonce_blockhash(&bank, &nonce_pubkey).unwrap()
    );
```
