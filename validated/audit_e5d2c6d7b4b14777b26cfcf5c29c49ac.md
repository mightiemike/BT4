### Title
Relayer fee refund in the NEAR Wallet Contract is fire-and-forget with no accounting for owed-but-unpaid fees - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
In the NEAR Wallet Contract (the eth-implicit-account wallet used to emulate Ethereum transactions on NEAR), `inner_rlp_execute` computes a relayer fee (`tx_fee`) and sends it to the relayer via an independent, unchecked `promise_batch_action_transfer`. If the wallet account's balance cannot cover this transfer, the fee-payment receipt simply fails silently — the relayer is never compensated, no record of the shortfall is kept, and there is no facility for the relayer to later claim the owed fee. This mirrors the reported `TokenSender.send()` pattern: a "best effort" compensation payment that is dropped without tracking when the payer's balance is insufficient.

### Finding Description
`internal::parse_rlp_tx_to_action` computes `tx_fee` from the user-signed Ethereum transaction's `max_fee_per_gas * gas_limit` [1](#0-0) . This fee is meant as compensation to the relayer for submitting the transaction on the user's behalf: "this refund ... allows a user with $NEAR to use a relayer service from their wallet immediately without additional on-boarding."

In `inner_rlp_execute`, once the fee is known and the action kind is `EOABaseTokenTransfer` or `ERC20Transfer` with a non-zero fee, the fee payment is scheduled unconditionally and independently from the main action, with no balance check and no error handling: [2](#0-1) 

This is a `promise_batch_create` + `promise_batch_action_transfer` call — a brand-new, separate receipt from the wallet account to the relayer (`context.predecessor_account_id`). Nothing in the contract inspects the result of this transfer: there is no `.then()` callback attached to it, unlike every other promise in this contract (e.g. `rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback` all use `.then()` and check `env::promise_result`). If the wallet's NEAR balance is insufficient to cover this `Transfer` action (e.g., depleted by prior transactions, storage staking requirements, or a large main action executed in the same call), the `Transfer` action underlying this receipt fails at runtime with `NotEnoughBalance`/`LackBalanceForState`, and the fee is simply never paid — with zero record kept anywhere in the `WalletContract` state (`nonce`, `has_in_flight_tx`) that a fee was owed.

Because this refund receipt is entirely separate from the main action's receipt chain (the main action's own promise chain proceeds through `rlp_execute_callback` regardless), the user's underlying action (transfer/ERC-20 transfer) can succeed while the relayer's fee silently fails to be paid. There is no mapping of "relayer owed fee X for tx nonce Y," and no method exposed by the contract to let a relayer later claim a fee once the wallet is topped up.

### Impact Explanation
This is a fee-bypass condition reachable by any relayer serving an eth-implicit wallet's meta-transaction:
- A relayer that submits a transaction on behalf of a user, expecting to be compensated via `tx_fee`, can receive nothing if the wallet's NEAR balance is exhausted (e.g., by the main action's deposit/gas, or a preceding transaction in the same block), even though the user signed a transaction promising that fee.
- Unlike gas refunds and deposit refunds in the core runtime, which are handled via `Receipt::new_balance_refund`/`Receipt::new_gas_refund` with well-defined burn-on-failure semantics documented in `docs/RuntimeSpec/Refunds.md`, this application-level fee payment has no such safety net or accounting: it is a plain unchecked `Transfer` action, and its failure produces no on-chain record.
- This directly mirrors the reported issue's core defect: a promised compensation payment that can silently fail when the payer's balance is insufficient, with no tracking mechanism enabling the owed party to reclaim it later. As in the original finding, the severity is bounded because the primary requested action (the user's transfer/ERC-20 call) still executes; the loss is limited to the relayer's fee compensation, not user funds.

### Likelihood Explanation
This is triggerable by any relayer/user pair reachable through a single submitted transaction (the wallet contract's `rlp_execute` entry point, callable by anyone holding the private key or an authorized access key) — no privileged, adversarial-node, or sync assumptions are required. The wallet contract's balance is not guaranteed to always exceed `tx_fee` plus the main action's `value`/gas costs, especially for wallets that are lazily funded per-transaction or drained close to zero by design (a common eth-wallet UX pattern), making the insufficient-balance branch practically reachable, not merely theoretical.

### Recommendation
Do not treat the relayer-fee transfer as fire-and-forget. Either:
1. Attach a `.then()` callback to the fee-transfer promise batch and, on `PromiseResult::Failed`, record the shortfall (e.g., an owed-fee ledger keyed by relayer account) in the `WalletContract` state, exposing a method for the relayer to reclaim the fee once the wallet is funded; or
2. Verify (via `env::account_balance()`) that the wallet can cover `tx_fee` plus the cost of the main action before scheduling the fee-transfer promise, and if not, fail the whole `rlp_execute` call up front so the relayer can decide whether to still relay the transaction; or
3. Bundle the fee-transfer as part of the same atomic action batch as the main action (rather than a wholly separate receipt) so that either both succeed or both roll back, giving the relayer a clear failure signal instead of a silent shortfall.

### Proof of Concept
1. Deploy the wallet contract as an eth-implicit account with a small NEAR balance, just barely covering the main action's transfer amount plus normal gas.
2. A user signs an Ethereum-style ERC-20 or base-token-transfer transaction with a non-zero `max_fee_per_gas`/`gas_limit` (yielding a non-zero `tx_fee`), intending to compensate the relayer.
3. A relayer submits this via `rlp_execute`. `internal::parse_rlp_tx_to_action` computes `tx_fee` [3](#0-2) , and `inner_rlp_execute` schedules the unchecked fee-transfer promise to the relayer [4](#0-3) .
4. Because the wallet's balance is fully consumed by the main action, the fee-transfer `Transfer` action fails on execution (`NotEnoughBalance`).
5. The main action's promise chain (via `.then(ext.rlp_execute_callback(...))`) still resolves successfully and returns `ExecuteResponse { success: true, .. }` to the caller.
6. The relayer's fee-transfer receipt fails permanently; no state in `WalletContract` reflects the shortfall, and the relayer has no on-chain way to reclaim the fee later.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L54-64)
```rust
    // Compute the fee based on the user's Ethereum transaction.
    // This is sent as a refund to the relayer in the case of an emulated base token
    // transfer or ERC-20 transfer. The reason for this refund is that it allows a
    // user with $NEAR to use a relayer service from their wallet immediately without
    // additional on-boarding.
    let tx_fee = {
        // Limit the cost by `VALUE_MAX` since we will convert this to a $NEAR amount.
        // The call to `low_u128` is safe because `VALUE_MAX` is the largest accepted value.
        let wei_amount = tx.max_fee_per_gas.saturating_mul(tx.gas_limit).min(VALUE_MAX).low_u128();
        NearToken::from_yoctonear(wei_amount.saturating_mul(MAX_YOCTO_NEAR as u128))
    };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L367-385)
```rust
            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                fee,
                ..
            })
            | TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) =
                &transaction_kind
            {
                if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
                    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
                    env::promise_batch_action_transfer(refund_promise, *fee);
                }
            }
```
