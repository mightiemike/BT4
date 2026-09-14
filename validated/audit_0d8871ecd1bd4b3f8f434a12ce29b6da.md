### Title
Hardcoded/misconfigured `ADDRESS_REGISTRAR_ACCOUNT_ID` in the Wallet Contract permanently fails the registrar lookup, allowing repeated fee drain via replayed `rlp_execute` calls - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The Wallet Contract (NEP-518 ETH-implicit account contract) hardcodes the account ID of an "address registrar" contract at compile time via `include_str!`, analogous to the reported pattern of hardcoding an external contract's address in Solidity. If this hardcoded account is wrong, unregistered, or its `lookup` method is unreachable/incompatible, every "EOA base-token transfer to another wallet contract with a fee" (`EthEmulationKind::EOABaseTokenTransfer { address_check: Some(_), .. }`) permanently fails the cross-contract lookup call. Because the relayer fee is transferred out of the wallet **before** the lookup result is known, and the nonce is **not incremented** when the lookup call fails, a single valid signed transaction can be replayed indefinitely, draining the wallet's NEAR balance to whoever resubmits it, while the user's intended transfer never executes.

### Finding Description
`ADDRESS_REGISTRAR_ACCOUNT_ID` is baked into the wasm at build time: [1](#0-0) [2](#0-1) 

When a user's signed Ethereum-style transaction targets another eth-implicit account (`TargetKind::EthImplicit`), the contract must ask the registrar whether that address is actually a named account, to detect faulty relayers: [3](#0-2) 

In `inner_rlp_execute`, the relayer fee refund promise is dispatched to the *predecessor* (caller of `rlp_execute`, i.e. the relayer) unconditionally and independently of whether the registrar lookup will later succeed: [4](#0-3) 

Only afterwards is the registrar contract invoked, resolved from the hardcoded account ID, and chained to `address_check_callback`: [5](#0-4) 

Critically, the nonce is deliberately **not** incremented for this transaction kind until the callback resolves the registrar result: [6](#0-5) 

In `address_check_callback`, if the promise to the registrar fails (`PromiseResult::Failed` — exactly what happens if the hardcoded account doesn't exist, has no `lookup` method, or returns an incompatible response), the function clears the in-flight flag and returns an error **without ever incrementing the nonce**: [7](#0-6) 

Because the nonce is unchanged and `has_in_flight_tx` is reset to `false`, the exact same signed transaction bytes remain valid and can be resubmitted via `rlp_execute` by anyone who has them (this is a public function; a valid signed Ethereum-style tx is a bearer instrument once it exists, and the fee mechanism is explicitly designed to let *any* relayer serve it): [8](#0-7) 

Each replay independently retriggers the unconditional fee-refund transfer at lines 366-385 above, moving real NEAR value out of the wallet contract's balance, while the registrar lookup keeps failing forever (since the hardcoded registrar address is wrong/broken), so the intended action is never completed and the nonce is never consumed.

### Impact Explanation
This is a direct, protocol-level analog of the reported bug class ("incorrect hardcoded address causing calls to a non-existent/incorrect contract to fail"), but with materially worse consequences than a mere revert: because side effects (fee payment) are ordered *before* the failing external call's result is known, and failure does not consume the replay-protection nonce, the flaw enables **unauthorized, repeated extraction of NEAR from a user's ETH-implicit wallet account** to any party holding the signed transaction — a concrete unauthorized value movement reachable purely by a transaction/RPC caller invoking `rlp_execute`. In the worst case this can drain the wallet's entire balance.

### Likelihood Explanation
Likelihood depends entirely on `ADDRESS_REGISTRAR_ACCOUNT_ID` being wrong, unset, or later becoming stale/incompatible (e.g., registrar contract redeployed, upgraded, or removed) — the same operational/deployment-mistake class as the reported Solidity bug. Since the value is a compile-time constant baked separately per network (mainnet/testnet/localnet builds), a single misconfiguration at build/deploy time affects every eth-implicit wallet on that network simultaneously, and the exploit requires no special privilege — any holder of one signed user transaction (which is routinely shared with relayers by design) can trigger unlimited fee-drain replays.

### Recommendation
- Do not transfer the relayer fee before the registrar lookup result is known; move the fee-refund dispatch into `address_check_callback` only after confirming the lookup succeeded (registered/not-registered), or otherwise make the fee payment strictly contingent on the transaction being consumed (nonce incremented).
- On registrar-call `Failed`, treat it identically to other relayer faults: increment the nonce (or trigger `ban_relayer`) so a failing/misconfigured registrar cannot be exploited to replay a valid signed transaction indefinitely.
- Add runtime/deployment validation (or a fallback/self-check) that the configured `ADDRESS_REGISTRAR_ACCOUNT_ID` actually exists and implements `lookup` before allowing address-check-dependent transfers, rather than trusting the hardcoded value unconditionally.

### Proof of Concept
1. Deploy (or misconfigure) the wallet contract wasm with `ADDRESS_REGISTRAR_ACCOUNT_ID` pointing to a non-existent or incompatible account (mirrors the reported hardcoded-wrong-address scenario).
2. User signs one Ethereum-style base-token-transfer transaction from their eth-implicit account to another eth-implicit account, with `max_fee_per_gas * gas_limit` producing a non-zero `tx_fee` (per `internal.rs` lines 59-64), and shares the signed tx with a relayer as normal.
3. Attacker/relayer calls `rlp_execute(target, tx_bytes_b64)`. `inner_rlp_execute` dispatches the fee-refund transfer to the caller immediately (lib.rs lines 374-385), then calls the (broken) registrar; the lookup promise fails, `address_check_callback` returns `PromiseResult::Failed` without incrementing the nonce (lib.rs lines 140-159).
4. Attacker repeats step 3 with the identical `tx_bytes_b64` any number of times; each call passes nonce validation (`internal.rs` line 357) because the nonce was never advanced, and each call re-triggers the fee transfer, draining the wallet's NEAR balance while the user's intended transfer is never executed.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-159)
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
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
            },
        };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L358-365)
```rust
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L366-385)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID (L1-1)
```text
address-map.near
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L107-122)
```rust
        Ok((action, ParsableTransactionKind::EthEmulation(eth_emulation))) => {
            if let TargetKind::EthImplicit(address) = target_kind {
                // Even though the action was parsable, the target is another wallet contract,
                // so the action _must_ still be a base token transfer, but we need
                // to check if the target is not registered (otherwise the relayer is faulty).
                (
                    Action::Transfer { receiver_id: target.to_string(), yocto_near: 0 },
                    TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                        address_check: Some(address),
                        fee: tx_fee,
                    }),
                )
            } else {
                (action, TransactionKind::EthEmulation(eth_emulation.into()))
            }
        }
```
