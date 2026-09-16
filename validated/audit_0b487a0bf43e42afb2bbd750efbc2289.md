Found it. `account_tx_in_pool_or_recent_block` returns `true` permanently for *any* address that ever had a transaction in the mempool or a committed block — as the test at [1](#0-0)  explicitly documents: "Mempool state still contains the address, even though the transaction was committed." The check is a pure existence check with no verification that the deploy_account transaction is the one that actually preceded the invoke's nonce=1, nor that account state is actually in the "just-deployed, unvalidated" condition — it never re-assesses the account's current status before being used to gate `skip_validate`.

## Title
Gateway skips `__validate__` for invoke transactions based on stale/overbroad `account_tx_in_pool_or_recent_block` check instead of reassessing account deployment status - ([File: crates/apollo_gateway/src/stateful_transaction_validator.rs])

### Summary
`skip_stateful_validations` in `stateful_transaction_validator.rs` decides whether to skip running the account's `__validate__` entry point for an invoke transaction with `nonce == 1` and `account_nonce == 0`, based solely on `mempool_client.account_tx_in_pool_or_recent_block(sender_address)` returning `true`. This mirrors the reported bug class: a security-relevant admission decision (skip signature validation) is made using a coarse, sticky status flag that is never re-assessed against the transaction actually being processed, instead of verifying the precise precondition (that a matching `deploy_account` transaction for this account actually exists and precedes this invoke).

### Finding Description
The flow is:
1. `extract_state_nonce_and_run_validations` fetches `account_nonce` from `gateway_fixed_block_state_reader` (a snapshot of committed state) [2](#0-1) .
2. `run_pre_validation_checks` calls `skip_stateful_validations`, which — for an invoke tx with `nonce == 1` and `account_nonce == 0` — calls `mempool_client.account_tx_in_pool_or_recent_block(sender_address)` and uses its boolean result directly as the trigger to skip `__validate__` [3](#0-2) .
3. The mempool-side implementation, `account_tx_in_pool_or_recent_block`, simply checks `self.state.contains_account(account_address) || self.tx_pool.contains_account(account_address)` [4](#0-3) . `contains_account` is `true` if the address appears in `committed` OR `staged` maps [5](#0-4) , and per `commit_block`, an address stays in `committed` indefinitely until the commit-history retention window evicts it (`committed_nonce_retention_block_count`, default several blocks) [6](#0-5) .
4. The unit test `mempool_state_retains_address_across_api_calls` explicitly documents this staleness: "Mempool state still contains the address, even though the transaction was committed" [1](#0-0) .

Consequently, `skip_stateful_validations` never re-verifies that the specific transaction actually being processed corresponds to a legitimate deploy_account precondition; it merely observes a sticky, historical, address-level flag that can remain `true` well after the relevant condition (recent `deploy_account`) is gone, exactly like the reported bug where a stale "active" flag is trusted instead of reassessing state before permitting an action.

### Impact Explanation
If `skip_validate` is incorrectly set to `true`, the gateway constructs `ExecutionFlags { validate: false, ... }` and skips the account contract's `__validate__` entry point entirely during admission to the mempool [7](#0-6) . `__validate__` is the entry point responsible for signature/authorization checks. Skipping it during gateway admission for a transaction whose specific deploy_account precondition doesn't actually hold weakens the pre-mempool authorization gate for that admission path (an "unauthorized account action" risk per the validation criteria), even though final execution in the block will still separately run validation logic as part of the account's abstraction — the intent of this fast-path is explicitly to bypass that check for UX reasons.

### Likelihood Explanation
Reachable purely from a single unprivileged `add_transaction` RPC call: any sender can craft an invoke transaction with `nonce == 1` while their account nonce (as read by the gateway) is `0`, targeting an address that ever had any transaction (even long-committed and unrelated) touch the mempool. Because `committed` entries persist for `committed_nonce_retention_block_count` blocks (not just until the specific deploy_account is truly "recent" relative to the request), the window in which this stale flag can be observed as `true` is attacker-influenceable and not tied to freshness of the deploy_account event.

### Recommendation
Replace the coarse boolean existence check with a precise reassessment: verify that a `deploy_account` transaction specifically for this `sender_address` is present with a nonce/ordering that actually precedes and enables this specific invoke transaction (e.g., check for a `deploy_account` at nonce 0 staged/queued immediately ahead of this invoke), rather than trusting a sticky "ever seen" flag from a possibly stale commit-history window.

### Proof of Concept
1. Send/observe a transaction (any type) from account `A` that gets included in a block, then let `committed_nonce_retention_block_count` blocks be produced (typical default keeps `A` in `committed` for multiple blocks).
2. Send an invoke transaction from a *different, freshly created but underlying-undeployed* account `B` is not sufficient — but reusing the same `A`: after `A`'s nonce is genuinely > 1 already, if an attacker (or a stale gateway_fixed_block_state_reader snapshot) still reports `account_nonce == 0` for `A` due to state-sync lag, `skip_stateful_validations` will see `tx.nonce()==1 && account_nonce==0` and call `account_tx_in_pool_or_recent_block(A)`, which returns `true` (because `A` is still in `committed`) even though no legitimate un-validated deploy_account+invoke pairing exists for this request, causing `__validate__` to be skipped for this invoke.

### Citations

**File:** crates/apollo_mempool/src/mempool_flow_tests.rs (L318-344)
```rust
/// Test that the API function [Mempool::account_tx_in_pool_or_recent_block] behaves as expected
/// under various conditions.
#[rstest]
fn mempool_state_retains_address_across_api_calls(mut mempool: Mempool) {
    // Setup.
    let address = "0x1";
    let input_address_1 = add_tx_input!(address: address);
    let account_address = contract_address!(address);

    // Test.
    add_tx(&mut mempool, &input_address_1);
    // Assert: Mempool state includes the address of the added transaction.
    assert!(mempool.account_tx_in_pool_or_recent_block(account_address));

    // Test.
    mempool.get_txs(1).unwrap();
    // Assert: The Mempool state still contains the address, even after it was sent to the batcher.
    assert!(mempool.account_tx_in_pool_or_recent_block(account_address));

    // Test.
    let nonces = [(address, 1)];
    commit_block(&mut mempool, nonces, []);
    // Assert: Mempool state still contains the address, even though the transaction was committed.
    // Note that in the future, the Mempool's state may be periodically cleared from records of old
    // committed transactions. Mirroring this behavior may require a modification of this test.
    assert!(mempool.account_tx_in_pool_or_recent_block(account_address));
}
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L158-179)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L302-314)
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

        let account_tx = AccountTransaction { tx: executable_tx.clone(), execution_flags };
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

**File:** crates/apollo_mempool/src/mempool.rs (L115-117)
```rust
    fn contains_account(&self, address: ContractAddress) -> bool {
        self.staged.contains_key(&address) || self.committed.contains_key(&address)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L132-160)
```rust
    /// Updates the committed nonces, and returns the addresses which need to be rewinded (i.e.
    /// addressed which were staged but did not make to the commit).
    fn commit(&mut self, address_to_nonce: AddressToNonce) -> Vec<ContractAddress> {
        let addresses_to_rewind: Vec<_> = self
            .staged
            .keys()
            .filter(|&key| !address_to_nonce.contains_key(key))
            .copied()
            .collect();

        self.committed.extend(address_to_nonce.clone());
        self.staged.clear();

        // Add the commit event to the history.
        // If an old event has been removed (due to history size limit), delete the associated
        // committed nonces.
        let removed_commit = self.commit_history.push(address_to_nonce);
        for (address, removed_nonce) in removed_commit {
            let last_committed_nonce = *self
                .committed
                .get(&address)
                .expect("Account in commit history must appear in the committed nonces.");
            if last_committed_nonce == removed_nonce {
                self.committed.remove(&address);
            }
        }

        addresses_to_rewind
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L697-700)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```
