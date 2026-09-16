## Analog Vulnerability Found

### Title
Skip-validation heuristic checks weak "any tx exists" condition instead of "valid deploy_account tx exists," allowing invalid state-dependent invokes to bypass `__validate__` - ([File: crates/apollo_gateway/src/stateful_transaction_validator.rs])

### Summary
The external report's root cause is a classic "wrong predicate" bug: a contract checks for the *existence of a loosely related state* (deposits) instead of the *actual precondition* (debt) before performing a state-changing action, causing legitimate calls to revert. The sequencer contains an analogous pattern in `skip_stateful_validations()`, which decides whether to skip the `__validate__` entry point for an `Invoke` transaction based on a weak existence check — "does *any* transaction for this address exist in the mempool or a recent block" — instead of verifying that the specific precondition actually holds: that a *valid* `deploy_account` transaction for this address was submitted and will make the account exist.

### Finding Description
In `skip_stateful_validations`, when an incoming `Invoke` transaction has `nonce == 1` and the current on-chain `account_nonce == 0` (i.e., the account looks undeployed), the gateway skips running the account's `__validate__` entry point, based solely on: [1](#0-0) 

The comment explicitly documents the weakened check: *"It is sufficient to check if the account exists in the mempool since it means that either it has a deploy_account transaction or transactions with future nonces that passed validations."* This substitutes the correct, specific condition ("a deploy_account transaction for nonce 0 was submitted and will succeed, deploying the account") with a broader, weaker one ("some transaction exists for this address"), implemented via: [2](#0-1) 

This mirrors the reported bug class exactly: checking `deposits.length > 0` (existence of a loosely-related property) instead of the actually required condition (`debt != 0`) before performing the state-dependent action (`burn`). Here, the mempool checks *tx presence* instead of *deploy-account validity/success* before performing the state-dependent decision (`validate: false` in `ExecutionFlags`).

When the underlying assumption is wrong — for example, the "existing" transaction for the address is not actually a valid, executable `deploy_account` transaction that will bring the account into existence (it could be evicted, replaced, or rejected before inclusion) — the `Invoke` transaction is admitted into the mempool/block-building pipeline with `validate: false`: [3](#0-2) 

Execution then fails deep in the blockifier because the sender contract does not exist, as demonstrated by the existing test for this exact failure mode: [4](#0-3) 

### Impact Explanation
Because the `Invoke` transaction bypassed `__validate__` based on the incorrect predicate, it is accepted into the mempool/block pipeline holding the account's nonce=1 slot. If the corresponding `deploy_account` transaction never actually deploys the account (evicted, replaced via fee escalation by an unrelated transaction, or otherwise fails), the invoke transaction cannot execute successfully and occupies the nonce slot, blocking any legitimately signed nonce=1..N transactions from that sender until the stuck transaction is dropped/rewound by the mempool's gap-closing and commit-time cleanup logic (`commit_block` / `rewind_txs`). This is a temporary freezing of the account's ability to transact/confirm new transactions, analogous to the reported "temporary freezing of funds," reachable purely by an unprivileged sender submitting an ordinary `deploy_account` + `invoke` pair (a pattern the sequencer explicitly optimizes for UX, per `create_deploy_account_tx_and_invoke_tx`): [5](#0-4) 

### Likelihood Explanation
This path is exercised by ordinary, unprivileged senders any time they submit a `deploy_account` transaction immediately followed by an `invoke` transaction (nonce 1) — a common wallet UX pattern the code is explicitly designed to support. The weak "any tx in pool/recent block" predicate can diverge from "deploy_account will actually succeed" whenever mempool eviction, fee-escalation replacement, or delayed-declare interactions occur between submission and processing, which are all normal operational conditions rather than adversarial ones.

### Recommendation
Replace the existence-only check in `skip_stateful_validations` / `account_tx_in_pool_or_recent_block` with a precise check that the specific transaction backing nonce 0 for the address is indeed a `deploy_account` transaction (or has already been confirmed as such), rather than merely checking that *some* transaction for the address is present. This mirrors the correct fix for the reported bug: check the exact precondition (debt / a genuine deploy_account) rather than a loosely correlated proxy (deposits / any transaction).

### Proof of Concept
1. Submit `deploy_account_tx` for a fresh account address (nonce 0).
2. Immediately submit `invoke_tx` for the same address with nonce 1 (`create_deploy_account_tx_and_invoke_tx` pattern).
3. Before block inclusion, have the `deploy_account_tx` evicted/replaced/rejected (e.g., fee escalation replaces it with an unrelated tx from the same sender, or it is dropped from the mempool while `tx_pool.contains_account` still reports true from a lingering reference).
4. `skip_stateful_validations` still returns `true` because `account_tx_in_pool_or_recent_block` only checks address presence, not that the *specific* deploy_account tx is valid — see `crates/apollo_gateway/src/stateful_transaction_validator_test.rs` lines 151-190, which asserts `skip_validate` purely as a function of `contains_tx` (a boolean flag with no relation to transaction *type* or *validity*).
5. The invoke tx is executed with `validate: false`; execution fails with "is not deployed" as in `test_invoke_tx_from_non_deployed_account`, and the nonce-1 slot remains stuck until mempool rewind/cleanup logic runs. [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L303-314)
```rust
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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L440-456)
```rust
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
```

**File:** crates/apollo_mempool/src/mempool.rs (L697-700)
```rust
    pub fn account_tx_in_pool_or_recent_block(&self, account_address: ContractAddress) -> bool {
        self.state.contains_account(account_address)
            || self.tx_pool.contains_account(account_address)
    }
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L405-447)
```rust
fn test_invoke_tx_from_non_deployed_account(
    block_context: BlockContext,
    max_fee: Fee,
    default_all_resource_bounds: ValidResourceBounds,
    #[case] tx_version: TransactionVersion,
) {
    let TestInitData { mut state, account_address, contract_address: _, mut nonce_manager } =
        create_test_init_data(&block_context.chain_info, CairoVersion::Cairo0);
    // Invoke a function from the newly deployed contract.
    let entry_point_selector = selector_from_name("return_result");

    let non_deployed_contract_address = StarkHash::TWO;

    let tx_result = run_invoke_tx(
        &mut state,
        &block_context,
        invoke_tx_args! {
            max_fee,
            sender_address: account_address,
            calldata: calldata![
                non_deployed_contract_address, // Contract address.
                entry_point_selector.0,    // EP selector.
                felt!(1_u8),         // Calldata length.
                felt!(2_u8)          // Calldata: num.
            ],
            resource_bounds: default_all_resource_bounds,
            version: tx_version,
            nonce: nonce_manager.next(account_address),
        },
    );
    let expected_error = "is not deployed.";
    match tx_result {
        Ok(info) => {
            //  Make sure the error is because the account wasn't deployed.
            assert!(info.revert_error.unwrap().to_string().contains(expected_error));
        }
        Err(err) => {
            //  Make sure the error is because the account wasn't deployed.
            assert!(err.to_string().contains(expected_error));
            // We expect to get an error only when tx_version is 0, on other versions to revert.
            assert_eq!(tx_version, TransactionVersion::ZERO);
        }
    }
```

**File:** crates/apollo_integration_tests/src/utils.rs (L713-726)
```rust
/// Generates a deploy account transaction followed by an invoke transaction from the same account.
/// The first invoke_tx can be inserted to the first block right after the deploy_tx due to
/// the skip_validate feature. This feature allows the gateway to accept this transaction although
/// the account does not exist yet.
pub fn create_deploy_account_tx_and_invoke_tx(
    tx_generator: &mut MultiAccountTransactionGenerator,
    account_id: AccountId,
) -> Vec<RpcTransaction> {
    let undeployed_account_tx_generator = tx_generator.account_with_id_mut(account_id);
    assert!(!undeployed_account_tx_generator.is_deployed());
    let deploy_tx = undeployed_account_tx_generator.generate_deploy_account();
    let invoke_tx = undeployed_account_tx_generator.generate_trivial_rpc_invoke_tx(1);
    vec![deploy_tx, invoke_tx]
}
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator_test.rs (L151-190)
```rust
#[rstest]
#[case::should_skip_validation(
    executable_invoke_tx(invoke_tx_args!(nonce: nonce!(1))),
    nonce!(0),
    true,
    false
)]
#[case::should_not_skip_validation_nonce_zero(
    executable_invoke_tx(invoke_tx_args!(nonce: nonce!(0))),
    nonce!(0),
    true,
    true
)]
#[case::should_not_skip_validation_nonce_over_one(
    executable_invoke_tx(invoke_tx_args!(nonce: nonce!(2))),
    nonce!(0),
    true,
    true
)]
// TODO(Arni): Fix this test case. Ideally, we would have a non-invoke transaction with tx_nonce 1
// and account_nonce 0. For deploy account the tx_nonce is always 0. Replace with a declare tx.
#[case::should_not_skip_validation_non_invoke(
    executable_deploy_account_tx(deploy_account_tx_args!()),
    nonce!(0),
    true,
    true

)]
#[case::should_not_skip_validation_account_nonce_1(
    executable_invoke_tx(invoke_tx_args!(nonce: nonce!(1))),
    nonce!(1),
    true,
    true
)]
#[case::should_not_skip_validation_no_tx_in_mempool(
    executable_invoke_tx(invoke_tx_args!(nonce: nonce!(1))),
    nonce!(0),
    false,
    true
)]
```
