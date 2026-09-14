## Title
Wallet Contract double-pays predecessor: pre-execution relayer fee refund plus post-failure full deposit refund drains the account's own $NEAR balance - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (`near-wallet-contract`) emulates Ethereum EOA behavior on NEAR by accepting an RLP-encoded transaction plus an attached NEAR deposit via `rlp_execute`. For emulated base-token/ERC-20 transfers with a non-zero relayer `fee`, the contract immediately sends the `fee` to the predecessor (relayer) from the wallet-contract account's own balance, **before** knowing whether the underlying action will succeed. Independently, it snapshots the **entire** `attached_deposit` (not `attached_deposit - fee`) into `CallerDeposit`, to be refunded in full to the same predecessor account if the subsequent action fails. If the inner action then fails, the predecessor receives the fee (already sent) **and** the full original deposit (refunded), i.e., strictly more value out than was attached in, funded from the wallet-contract account's own balance.

### Finding Description
In `inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:330-410`):
1. `let caller_deposit = CallerDeposit::new(&context);` captures the **full** `env::attached_deposit()` (`runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs:180-191`).
2. If the parsed transaction is an `EOABaseTokenTransfer` or `ERC20Transfer` with a non-zero `fee`, the contract immediately fires a **separate, unconditional** promise transferring `fee` to `context.predecessor_account_id` (`lib.rs:374-385`), regardless of whether the main action later succeeds.
3. The main action promise is then chained to `rlp_execute_callback`, passing along the same `caller_deposit` (the full pre-fee deposit amount) (`lib.rs:412-471`).
4. In `rlp_execute_callback` (`lib.rs:275-317`), if the chained promise **fails**, the contract refunds the entire `caller_deposit.yocto_near` (the full attached deposit, not reduced by the fee already paid) back to the same `account_id` (the predecessor/relayer) (`lib.rs:296-305`).

Because the `fee` transfer at step 2 already happened unconditionally and independently of the outcome, and step 4 refunds the full attached deposit again on failure, the same predecessor account receives `fee + full_deposit` in the failure case, exceeding the `full_deposit` amount they originally attached. The excess (`fee`) is paid out of the wallet-contract account's own NEAR balance, which is otherwise meant to hold only the user's ETH-emulated "wallet" funds. Repeated failed transactions with a relayer fee let a predecessor systematically drain the wallet account's balance beyond what it deposits, each round netting the attacker `fee` yoctoNEAR paid by the wallet's own account.

This is functionally analogous to the fee-on-transfer/deflationary-token accounting bug: the contract records a "deposit" number for accounting/refund purposes that no longer matches the true remaining value after an unaccounted-for value movement (the immediate fee payout) has already occurred, producing an inflated refund relative to actual funds held.

### Impact Explanation
This allows unauthorized value extraction from the wallet-contract account's own NEAR balance whenever a relayer-fee-bearing eth-emulated transaction subsequently fails (e.g., a malicious or self-controlled relayer intentionally causes downstream action failure, such as targeting an unregistered/invalid receiver for an ERC-20 transfer, or letting an `EOABaseTokenTransfer` fail). Each failed attempt nets the caller extra `fee` yoctoNEAR beyond their attached deposit, funded by the wallet account. This is a concrete unauthorized value movement / fund-draining bug reachable by any unprivileged caller (relayer) submitting a crafted transaction to a deployed wallet contract, satisfying "Medium" severity per the reachable-analog criteria (native $NEAR loss, not merely a resource/gas issue).

### Likelihood Explanation
The wallet-contract explicitly designs for potentially malicious/self-interested relayers (see the extensive relayer-error handling and "ban_relayer" mechanism), and the code comments even acknowledge care is needed around fee handling with untrusted relayers. A predecessor account controlling both the relayer and being the one entitled to `caller_deposit` refunds (the common single-relayer-as-caller flow, or any external caller acting as relayer for itself) can trivially trigger a failing downstream promise (e.g., ERC-20 transfer to a bad receiver, or attaching insufficient gas causing the final action to fail after the fee promise already fired) to repeatedly claim the fee amount as free profit. This requires no special privileges beyond calling `rlp_execute`.

### Recommendation
- Deduct the already-paid `fee` from the amount tracked in `CallerDeposit` before scheduling the fee-refund promise, so the failure-path refund only returns `attached_deposit - fee`, matching what the wallet account actually still owes.
- Alternatively, defer sending the relayer `fee` until after the main action's outcome is known (pay it only on success, or net it out in the callback rather than as an unconditional up-front transfer).
- Add an invariant/test ensuring `fee_paid + refund_on_failure <= attached_deposit` for all `EOABaseTokenTransfer`/`ERC20Transfer` code paths.

### Proof of Concept
1. Deploy a wallet contract for an eth-implicit account funded with some balance.
2. Predecessor (acting as its own relayer) calls `rlp_execute` with `attached_deposit = D`, submitting an RLP-encoded ERC-20 `transfer` (or base-token transfer) with a non-zero `max_fee_per_gas * gas_limit` (`tx_fee = F`, `F > 0`), targeting an NEP-141 token contract.
3. `inner_rlp_execute` records `caller_deposit = D` (`types.rs:187-190`) and immediately transfers `F` to the predecessor (`lib.rs:381-384`).
4. Craft the `ft_transfer` call to fail downstream (e.g., point `target` at a token contract method that panics, or supply a receiver that causes the promise chain to fail at `nep_141_storage_balance_callback`/the transfer call itself).
5. `rlp_execute_callback` observes `PromiseResult::Failed` and refunds the full `D` back to the predecessor (`lib.rs:296-305`).
6. Net result: predecessor started with `D` attached, ends up having received `F + D` back, a `F` yoctoNEAR profit paid from the wallet-contract account's own balance, with no successful action performed.

Note: I was unable to execute this against a live near-workspaces test harness to empirically confirm the exact NEAR balance deltas (e.g., accounting for gas costs would need to be netted out in a real test), so this is based on static code-path analysis of `lib.rs`, `types.rs`, `internal.rs`, and `eth_emulation.rs`. A background Devin session with test execution (`runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs` harness) would be needed to produce a fully reproduced, on-chain-verified PoC. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-305)
```rust
        match env::promise_result(0) {
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
                }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
```
