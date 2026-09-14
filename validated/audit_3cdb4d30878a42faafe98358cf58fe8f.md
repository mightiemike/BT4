### Title
Misconfigured (hardcoded) `ADDRESS_REGISTRAR_ACCOUNT_ID` in the immutable ETH-Wallet global contract permanently disables nonce replay-protection and enables repeated fee draining - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (the code every ETH-implicit account runs) resolves the account it must call to validate a cross-wallet transfer target via a compile-time constant, `ADDRESS_REGISTRAR_ACCOUNT_ID`, baked into the WASM with `include_str!` and never configurable post-deployment [1](#0-0) . Exactly like the `StableOracleWBGL`/`StableOracleDAI` bug (a static, unmodifiable reference address set once and used for all subsequent cross-contract calls), if this baked-in account id is wrong or points to a non-existent/unresponsive account, every cross-contract call made against it will fail permanently, and there is no admin mechanism to fix it because the wallet contract is deployed as an immutable global contract addressed by its code hash [2](#0-1) .

### Finding Description
`rlp_execute` parses a signed Ethereum transaction and, when the target is another ETH-implicit account, must query the address registrar to confirm the relayer supplied the correct target before executing the action [3](#0-2) . In `inner_rlp_execute`, the nonce is deliberately **not** incremented for this transaction kind up front — incrementing is deferred to the `address_check_callback`, which only bumps the nonce once the registrar call succeeds and confirms the address is unregistered [4](#0-3) [5](#0-4) .

However, before that registrar call is even dispatched, if the transaction carries a non-zero relayer fee, the contract unconditionally schedules a NEAR transfer of that fee from the wallet account to the transaction's predecessor (the caller/relayer) [6](#0-5) .

The registrar account itself is resolved purely from the hardcoded string, parsed and used to build the cross-contract call: `ADDRESS_REGISTRAR_ACCOUNT_ID.trim().parse()...ext_registrar::ext(account_id)...lookup(...)` [7](#0-6) . If this constant is wrong (points to a nonexistent account, wrong network's registrar, or any account without a `lookup` method), the promise will always resolve as `PromiseResult::Failed`. In that case `address_check_callback` takes the early-return branch that reports failure and resets `has_in_flight_tx` — but never increments `self.nonce` [8](#0-7) .

Because the fee-refund transfer at lines 374-385 already executed unconditionally as part of the same `rlp_execute` call — before the (permanently failing) registrar lookup even resolves — and because the nonce guarding replay of that exact signed transaction never advances, any caller can resubmit the identical `rlp_execute(target, tx_bytes_b64)` call over and over. Each resubmission re-triggers the fee-refund transfer to whichever account calls it, since `has_in_flight_tx` is cleared once the (always-failing) registrar promise resolves [9](#0-8) , and the nonce comparison in `validate_tx_relayer_data`/`parse_rlp_tx_to_action` will still accept the same nonce because it was never consumed.

### Impact Explanation
This constitutes unauthorized, repeatable value movement out of a user's ETH-implicit wallet account: a misconfigured (or later-desynced) `ADDRESS_REGISTRAR_ACCOUNT_ID` — a hardcoded reference exactly analogous to the incorrect static oracle address in the reported Solidity bug — turns a one-time relayer fee into a repeatedly drainable payment, because the nonce-based replay protection that is supposed to guard this exact code path is contingent on the registrar call succeeding, which it structurally cannot if the address is wrong. Since the wallet contract is deployed globally by code hash and used by every ETH-implicit account, this is not a per-account or per-collateral issue but a network-wide immutable defect, worse than the original finding's scope (single collateral asset) because it affects the shared global contract used by all ETH-implicit accounts.

### Likelihood Explanation
This requires no privileged access: any account (a plain contract caller/transaction sender) can call the public `rlp_execute` method, and simply needs one validly-signed ETH-style transaction whose target resolves to `TargetKind::EthImplicit` with a non-zero fee, then repeatedly resubmit it via ordinary RPC/transaction calls. The only precondition is that the compiled-in `ADDRESS_REGISTRAR_ACCOUNT_ID` be wrong for the deployed environment — directly mirroring how the audited `StableOracleWBGL`/`StableOracleDAI` bug arose from a bad hardcoded constant baked in at construction/build time with no correction path.

### Recommendation
- Do not gate replay protection (`nonce` increment) on the success of an external cross-contract call whose target address is a compile-time constant; increment the nonce (or otherwise consume replay protection) as soon as the transaction is admitted for processing, regardless of the registrar call's outcome, or make the fee-refund transfer conditional on the registrar check succeeding.
- Add build/deployment-time verification (and ideally a runtime self-test or health check) that `ADDRESS_REGISTRAR_ACCOUNT_ID` resolves to a live, correct registrar contract before the global contract hash is published/used in production, since [1](#0-0)  cannot be corrected post-deployment without shipping an entirely new global contract hash.

### Proof of Concept
1. Assume `ADDRESS_REGISTRAR_ACCOUNT_ID` is baked incorrectly at build time (e.g., points to `address-map.near` on an environment where that account does not exist or does not implement `lookup`), per [10](#0-9) .
2. A user signs one ETH-style transaction transferring value with a nonzero relayer fee, targeting another ETH-implicit account (forcing `EOABaseTokenTransfer { address_check: Some(_), fee, .. }`).
3. Any caller submits it via `rlp_execute(target, tx_bytes_b64)`. `inner_rlp_execute` schedules the fee transfer to the predecessor immediately [6](#0-5)  and does not bump the nonce [4](#0-3) , then dispatches the registrar `lookup` call to the broken hardcoded account [7](#0-6) .
4. The registrar call fails (`PromiseResult::Failed`); `address_check_callback` reports failure without incrementing the nonce [8](#0-7) , and clears `has_in_flight_tx`.
5. The caller resubmits the identical `rlp_execute` call with the same nonce; step 3-4 repeat, and the fee transfer fires again — indefinitely, draining the wallet's balance one fee payment at a time.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-148)
```rust
        self.has_in_flight_tx = false;
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L174-178)
```rust
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L356-365)
```rust
            // the users tokens because only one transaction is allowed to be in-flight
            // at a time.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L374-385)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-431)
```rust
    let promise = match transaction_kind {
        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
            address_check: Some(address),
            ..
        }) => {
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
            let address = format!("0x{}", hex::encode(address));
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
```

**File:** runtime/runtime/src/contract_code.rs (L58-66)
```rust
            if LegacyEthWallet::resolve(local_hash).is_some() {
                // ETH implicit wallet accounts use global contracts, including
                // those created in old protocol versions.
                let global_hash = eth_wallet_global_contract_hash(chain_id);
                return Ok(RuntimeContractIdentifier::Global {
                    code_hash: global_hash,
                    identifier: GlobalContractIdentifier::CodeHash(global_hash),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID (L1-1)
```text
address-map.near
```
