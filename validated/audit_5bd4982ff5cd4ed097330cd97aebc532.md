### Title
Unauthorized Account Execution via Signature-Validation Bypass in `skip_stateful_validations` — ([File: crates/apollo_gateway/src/stateful_transaction_validator.rs])

### Summary
The gateway's stateful validation logic contains a UX feature that skips the account contract's `__validate__` entry point for an `invoke` transaction with `nonce == 1` when the account's on-chain nonce is still `0`, provided *any* transaction from that sender address is already present in the mempool. This check does not verify that the pending mempool transaction is the account's own `deploy_account` transaction, nor that the `invoke` transaction being admitted was submitted by the same party. Consequently, once a legitimate `deploy_account` transaction for a not-yet-deployed account is visible in the mempool, any unprivileged third party can front-run the account owner by submitting a crafted `invoke` transaction with `nonce = 1`, an arbitrary/garbage signature, and attacker-chosen calldata targeting the same (not-yet-deployed) account. Because signature validation (`__validate__`) is skipped for this transaction, it will be admitted and, once the `deploy_account` commits ahead of it in the same block, executed as an authorized action of the victim account — without the account owner ever having approved it.

### Finding Description
The relevant logic lives in two cooperating pieces:

1. `skip_stateful_validations` in `crates/apollo_gateway/src/stateful_transaction_validator.rs` decides whether to bypass the account's validate entry point: [1](#0-0) 

It only checks:
- `tx.nonce() == 1` and `account_nonce == 0` (i.e., this looks like the second transaction of a deploy+invoke bundle), and
- `mempool_client.account_tx_in_pool_or_recent_block(sender_address)` — i.e., *some* transaction from that address exists in the mempool or was recently committed.

2. `account_tx_in_pool_or_recent_block` in `crates/apollo_mempool/src/mempool.rs` is a coarse existence check with no linkage to the specific `deploy_account` transaction, its hash, or its submitter: [2](#0-1) 

3. The result feeds directly into disabling the `validate` flag used to skip the account contract's `__validate__` call: [3](#0-2) [4](#0-3) 

The same pattern (and same missing linkage) exists in the Python/Cairo native path used by the OS/legacy validator: [5](#0-4) 

The intended use case (explicitly documented in the code comment) is to let a wallet submit `deploy_account` + `invoke` together and have the second transaction succeed even though the account isn't deployed yet at admission time — improving UX. However, nothing in the check ties the admitted `invoke` to the specific `deploy_account` transaction: it is triggered by the mere presence of *any* address-matching transaction in the pool. Since the mempool's `tx_pool.contains_account`/`add_tx` path does not authenticate the transaction's signature (signature/`__validate__` checks only occur inside the stateful validator, which is exactly what gets skipped here), an attacker who observes a pending `deploy_account` transaction for some address (mempool contents are visible via RPC/gossip) can submit their own `invoke` transaction with `nonce = 1` for that same address, containing arbitrary calldata and an invalid/garbage signature. Because `skip_stateful_validations` returns `true`, `run_validate_entry_point` sets `validate: false` and the account's authorization check is never performed for that transaction.

### Impact Explanation
This breaks the fundamental Starknet account-abstraction invariant that every state-mutating call on an account must be authorized by that account's `__validate__` logic (i.e., a valid signature). An attacker can:
- Race a victim's genuine `deploy_account`+`invoke` bundle and get their own malicious `invoke` (nonce 1) admitted and executed instead of/alongside the victim's, since nonce 1 is a single slot and whichever transaction lands there is what executes.
- Drive arbitrary calls "as" the newly-deployed account (e.g., transferring any funds pre-funded to the deployment address, approving allowances, or interacting with other contracts) with no valid signature, resulting in concrete loss of funds and an unauthorized account action — squarely in the "no signature verification enforced" bug class analogous to the Shopware double opt-in bypass (a verification gate silently skipped based on an insufficiently specific precondition).

### Likelihood Explanation
Exploitation requires only:
- Observing a pending `deploy_account` transaction for a target address (public mempool/gossip data), and
- Submitting an ordinary `invoke` transaction with `nonce = 1` and arbitrary signature/calldata before the legitimate nonce‑1 transaction is admitted.

No special privileges, staking, or protocol-level access are required — this is reachable by any unprivileged transaction sender through the normal gateway/mempool submission path, making likelihood high whenever deploy-and-invoke bundling is used (a common wallet UX pattern for funding freshly generated accounts).

### Recommendation
Do not gate the validation-skip decision on a coarse "any tx from this address exists in pool" check. Require a cryptographic/structural link between the `deploy_account` transaction and the `invoke` transaction being admitted (e.g., only skip validation for the specific transaction hash pair the client submitted together, or require the deploy_account transaction's hash to be supplied and matched, as is partially attempted via the `deploy_account_tx_hash` parameter in `py_validator.rs` but not enforced against mempool contents). At minimum, `account_tx_in_pool_or_recent_block` should be replaced with a check that the *only* pending transaction for that address is the expected `deploy_account` transaction, and that the `invoke` transaction being validated was submitted in the same batch/request as that `deploy_account` transaction.

### Proof of Concept
1. Victim generates an account address and sends funds to it, then submits a valid `deploy_account` transaction (nonce 0) to the gateway. It is admitted into the mempool.
2. Attacker observes this pending `deploy_account` transaction (address, nonce) via mempool/RPC visibility.
3. Attacker crafts an `invoke` transaction with `sender_address = victim_address`, `nonce = 1`, arbitrary/garbage `signature`, and calldata calling e.g. an ERC20 `transfer` to the attacker's address, and submits it to the gateway before the victim's own nonce‑1 transaction.
4. In `extract_state_nonce_and_run_validations` → `run_pre_validation_checks` → `skip_stateful_validations`, since `tx.nonce() == 1`, `account_nonce == 0`, and `account_tx_in_pool_or_recent_block(victim_address) == true` (due to the pending `deploy_account`), `skip_validate` returns `true`.
5. `run_validate_entry_point` sets `validate: false`, so `blockifier_validator.validate(account_tx)` never invokes the account's `__validate__`, and the attacker's transaction is admitted to the mempool despite the invalid signature.
6. When the block is built, the victim's `deploy_account` (nonce 0) executes first, deploying the account; the attacker's `invoke` (nonce 1) then executes with `__execute__` only, performing the attacker-chosen action as an authorized action of the victim's account.

### Citations

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L399-410)
```rust
    async fn run_pre_validation_checks(
        &self,
        executable_tx: &ExecutableTransaction,
        account_nonce: Nonce,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<bool> {
        self.validate_state_preconditions(executable_tx, account_nonce).await?;
        validate_by_mempool(executable_tx, account_nonce, mempool_client.clone()).await?;
        let skip_validate =
            skip_stateful_validations(executable_tx, account_nonce, mempool_client.clone()).await?;
        Ok(skip_validate)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L429-460)
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
    }

    Ok(false)
```

**File:** crates/apollo_mempool/src/mempool.rs (L697-700)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/native_blockifier/src/py_validator.rs (L92-121)
```rust
impl PyValidator {
    // Returns whether the transaction should be statefully validated.
    // If the DeployAccount transaction of the account was submitted but not processed yet, it
    // should be skipped for subsequent transactions for a better user experience. (they will
    // otherwise fail solely because the deploy account hasn't been processed yet).
    #[allow(clippy::result_large_err)]
    pub fn should_run_stateful_validations(
        &mut self,
        account_tx: &AccountTransaction,
        deploy_account_tx_hash: Option<TransactionHash>,
    ) -> StatefulValidatorResult<bool> {
        if account_tx.tx_type() != TransactionType::InvokeFunction {
            return Ok(true);
        }
        let tx_info = account_tx.create_tx_info();
        let nonce = self.stateful_validator.get_nonce(tx_info.sender_address())?;

        let deploy_account_not_processed =
            deploy_account_tx_hash.is_some() && nonce == Nonce(Felt::ZERO);
        let tx_nonce = tx_info.nonce();
        let is_post_deploy_nonce = Nonce(Felt::ONE) <= tx_nonce;
        let nonce_small_enough_to_qualify_for_validation_skip =
            tx_nonce <= self.max_nonce_for_validation_skip;

        let skip_validate = deploy_account_not_processed
            && is_post_deploy_nonce
            && nonce_small_enough_to_qualify_for_validation_skip;

        Ok(!skip_validate)
    }
```
