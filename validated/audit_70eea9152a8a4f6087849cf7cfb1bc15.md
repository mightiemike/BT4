### Title
Deploy-account UX "skip validate" heuristic lets an unprivileged tx grief a legitimate deploy_account+invoke pair via nonce occupation - ([File: crates/apollo_gateway/src/stateful_transaction_validator.rs])

### Summary
The gateway's `skip_stateful_validations` / `skip_validate` heuristic, used to improve UX for `deploy_account` + `invoke` bundles, bypasses the invoke transaction's `__validate__` (signature) check at mempool-admission time whenever an invoke tx has `nonce == 1` and the sender's on-chain nonce is `0`, as long as *any* transaction for that address already exists in the mempool (typically the paired `deploy_account` tx). Because the target account address for a `deploy_account` transaction is deterministic and derivable from its (public) constructor calldata and salt visible in the gateway/mempool, an unprivileged attacker can observe a pending `deploy_account` transaction and race it with a bogus `invoke` transaction at `nonce=1` for the same not-yet-deployed address, with an arbitrary/invalid signature.

### Finding Description
`skip_stateful_validations` decides to skip `__validate__` based purely on `(tx.nonce() == 1, account_nonce == 0)` and the presence of *any* transaction for the sender address in the mempool/recent-block set, not on whether the specific invoke transaction actually pairs correctly with the account being deployed: [1](#0-0) 

This flag then feeds `run_validate_entry_point`, which sets `ExecutionFlags.validate = !skip_validate`, meaning `__validate__` is not run at all for the admission-time check: [2](#0-1) 

Because the attacker's forged invoke tx passes gateway admission without a valid signature, it reaches the mempool's `add_tx`, occupying the `(address, nonce=1)` slot via `validate_incoming_tx` / `validate_fee_escalation`'s `DuplicateNonce` guard: [3](#0-2) 

When the legitimate account owner later submits their real `invoke` transaction at `nonce=1` (the actual continuation of their `deploy_account`), it is rejected with `MempoolError::DuplicateNonce` (or, if fee escalation is enabled, must out-bid the attacker's junk tx, which has no real cost constraint since it need not be a "real" fee-paying transaction that survives execution) as shown by the mempool's existing duplicate/fee-escalation logic: [4](#0-3) 

This is structurally the same bug class as the CrabNetting report: a piece of state (here, the account's `nonce=1` mempool slot) that is supposed to be reserved for the legitimate signer can be consumed by anyone, because the check meant to gate admission (signature validation) is intentionally skipped for a specific case, and the case is identified by public, front-runnable information (an address + nonce pair visible from a pending `deploy_account` tx in the mempool).

### Impact Explanation
A successful griefer prevents the specific account's `invoke` transaction from ever being included, effectively freezing/blocking that user's intended action (e.g., a funded first transaction right after deployment) until they resubmit with a bumped nonce workaround or the attacker's junk tx expires (mempool TTL) — a denial of service on a legitimate, unprivileged sender's transaction. This matches the "concrete... permanent freezing" / "network unable to confirm new transactions" (for this specific account) bar, though the blast radius is limited to accounts using the deploy_account+invoke UX shortcut rather than the whole network.

### Likelihood Explanation
Low cost to the attacker: they only need to observe the pending `deploy_account` tx (its target address is computable from public salt/class-hash/constructor calldata) and submit a single crafted `invoke` transaction with `nonce=1` and a bogus signature before the legitimate invoke tx lands. No special privileges or private keys are required, satisfying "unprivileged transaction sender" reachability. However, exploitation window is narrow (must race the specific bundle) and impact is confined per-account, which typically bounds severity to Medium rather than network-wide Critical/High.

### Recommendation
Do not rely solely on "any tx present in the mempool for this address" as the skip-validate signal. Instead, tie the skip to the *specific* deploy_account transaction hash (or the exact expected invoke tx hash paired with a specific deploy_account submission) so an unrelated/forged invoke transaction cannot exploit the exemption, and/or still enforce basic signature format/length checks even when `__validate__` execution is skipped, and/or require the skipped-validation invoke tx to be dropped/re-validated normally once the paired deploy_account is confirmed, freeing the nonce slot immediately if the invoke fails on-chain validation rather than only on eventual TTL expiry.

### Proof of Concept
1. Attacker watches the gateway/mempool and observes a `deploy_account` transaction for address `A` (salt/class_hash/constructor calldata are public), computing `A` deterministically.
2. Attacker crafts an `invoke` transaction: `sender_address = A`, `nonce = 1`, arbitrary/garbage `signature`, arbitrary calldata.
3. Attacker submits this tx to the gateway before the account owner's real `invoke` (nonce=1) is received. `skip_stateful_validations` returns `true` because `tx.nonce()==1`, `account_nonce==0`, and the account already has a tx (`deploy_account`) in the mempool — so `__validate__` is skipped per `crates/apollo_gateway/src/stateful_transaction_validator.rs:429-457` and `:302-312`.
4. The forged tx is admitted into the mempool and occupies `(A, nonce=1)`.
5. When the real owner submits their genuine `invoke` tx at `nonce=1`, `Mempool::add_tx` rejects it with `MempoolError::DuplicateNonce` per the guard in `crates/apollo_mempool/src/mempool.rs:756-792`, denying the legitimate transaction until the attacker's junk tx is cleared (mempool TTL, or execution-time rejection during block building — which happens later and does not restore the user's rejected submission).

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L302-312)
```rust
    #[sequencer_latency_histogram(GATEWAY_VALIDATE_TX_LATENCY, true)]
    async fn run_validate_entry_point(
        &mut self,
        executable_tx: &ExecutableTransaction,
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L429-457)
```rust
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        // check if the transaction nonce is 1, meaning it is post deploy_account, and the
        // account nonce is zero, meaning the account was not deployed yet.
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            let account_address = tx.sender_address();
            debug!("Checking if deploy_account transaction exists for account {account_address}.");
            // We verify that a deploy_account transaction exists for this account. It is sufficient
            // to check if the account exists in the mempool since it means that either it has a
            // deploy_account transaction or transactions with future nonces that passed
            // validations.
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
                .inspect(|exists| {
                    if *exists {
                        debug!("Found deploy_account transaction for account {account_address}.");
                    } else {
                        debug!(
                            "No deploy_account transaction found for account {account_address}."
                        );
                    }
                });
        }
```

**File:** crates/apollo_mempool/src/mempool.rs (L756-792)
```rust
    /// Validates whether the incoming transaction may replace an existing one at the same
    /// `(address, nonce)` via fee escalation, without mutating any state. Returns the existing
    /// transaction to be replaced when a valid replacement exists, `None` when there is nothing to
    /// replace, or an error when a replacement is present but not permitted.
    fn validate_fee_escalation(
        &self,
        incoming_tx_reference: TransactionReference,
    ) -> MempoolResult<Option<TransactionReference>> {
        let TransactionReference { address, nonce, .. } = incoming_tx_reference;

        self.validate_no_delayed_declare_front_run(incoming_tx_reference)?;

        if !self.config.static_config.enable_fee_escalation {
            if self.tx_pool.get_by_address_and_nonce(address, nonce).is_some() {
                return Err(MempoolError::DuplicateNonce { address, nonce });
            };

            return Ok(None);
        }

        let Some(existing_tx_reference) = self.tx_pool.get_by_address_and_nonce(address, nonce)
        else {
            // Replacement irrelevant: no existing transaction with the same nonce for address.
            return Ok(None);
        };

        if !self.should_replace_tx(&existing_tx_reference, &incoming_tx_reference) {
            info!(
                "{existing_tx_reference} was not replaced by {incoming_tx_reference} due to \
                 insufficient fee escalation."
            );
            // TODO(Elin): consider adding a more specific error type / message.
            return Err(MempoolError::DuplicateNonce { address, nonce });
        }

        Ok(Some(existing_tx_reference))
    }
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L514-539)
```rust
// TODO(Elin): reconsider this test in a more realistic scenario.
#[rstest]
fn test_validate_and_add_tx_rejects_duplicate_tx_hash(mut mempool: Mempool) {
    // Setup.
    let input = add_tx_input!(tx_hash: 1, tx_nonce: 1, account_nonce: 0);
    // Same hash is possible if signature is different, for example.
    // This is an artificially crafted transaction with a different nonce in order to skip
    // replacement logic.
    let duplicate_input = add_tx_input!(tx_hash: 1, tx_nonce: 2, account_nonce: 0);

    // Test.
    validate_tx(&mut mempool, &ValidationArgs::from(&input));
    add_tx(&mut mempool, &input);

    let expected_error = MempoolError::DuplicateTransaction { tx_hash: input.tx.tx_hash() };
    validate_tx_expect_error(
        &mut mempool,
        &ValidationArgs::from(&duplicate_input),
        expected_error.clone(),
    );
    add_tx_expect_error(&mut mempool, &duplicate_input, expected_error);

    // Assert: the original transaction remains.
    let expected_mempool_content = MempoolTestContentBuilder::new().with_pool([input.tx]).build();
    expected_mempool_content.assert_eq(&mempool.content());
}
```
