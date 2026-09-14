### Title
Permanent lock of the NEAR wallet contract's `has_in_flight_tx` guard freezes all future eth-emulated transactions - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `near-wallet-contract` (the eth-implicit account contract that lets an Ethereum-style signed transaction move a NEAR account's funds/actions) guards against concurrent execution with a single boolean field, `has_in_flight_tx`. It is set to `true` before a cross-contract callback chain is scheduled and is only ever reset to `false` inside the corresponding callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`). Because NEAR only persists a receipt's state changes if that receipt succeeds, any failure of the callback receipt (including running out of gas while it is itself scheduling the next promise) discards the `has_in_flight_tx = false` write together with everything else the callback did. This leaves the flag permanently stuck at `true`, and `rlp_execute` unconditionally rejects every future call while the flag is `true`, with no other method able to clear it. This is the "broken hook blocks user funds" bug class from the report, translated to NEAR's promise/callback model.

### Finding Description
`rlp_execute` sets `self.has_in_flight_tx = true` and returns a `Promise` whenever it needs to call out to another contract (the address registrar, or a NEP-141 token for `storage_balance_of`/`storage_deposit`/`ft_transfer`): [1](#0-0) 

Every hook/callback that is supposed to clear the flag does so as its *first* statement, then goes on to potentially schedule further promises using attacker-influenced gas values taken from the decoded Ethereum action (`action.gas()`): [2](#0-1) [3](#0-2) 

If the callback function itself fails after clearing the flag — for example if `with_static_gas(callback_gas)` (`callback_gas` includes `action.gas()`, an attacker/relayer-controlled value from the signed Ethereum transaction) tries to schedule more gas than is actually left in the receipt, causing `GasExceeded`/"Exceeded the prepaid gas" — the entire receipt fails. The runtime's apply loop only commits state changes for receipts that succeed; on any error the whole `TrieUpdate` for that receipt, including the early `has_in_flight_tx = false` write, is rolled back: [4](#0-3) 

That "Exceeded the prepaid gas" is a real, reachable failure mode of exactly this call chain is demonstrated by the contract's own test: [5](#0-4) 

In that test the failure happens in the very first `rlp_execute` receipt, before `has_in_flight_tx` is ever set, so the contract remains usable afterward. However, the same class of gas-exceeded failure can also occur later, inside `address_check_callback` or `nep_141_storage_balance_callback`, i.e. *after* `has_in_flight_tx` has already been set to `true` by the initiating `rlp_execute` receipt. In that case, rolling back the failing callback receipt does not undo the earlier, already-committed `has_in_flight_tx = true`, so the flag survives while the fix (`has_in_flight_tx = false`) is discarded.

Once this happens, every subsequent call to `rlp_execute` is rejected unconditionally at the top of the function, before any other logic runs: [6](#0-5) 

There is no admin, owner, or emergency-exit method in the contract that can reset `has_in_flight_tx`; the only other place it is cleared is `ban_relayer`, which is `#[private]` and only reachable as part of the same broken promise chain: [7](#0-6) 

### Impact Explanation
The wallet contract is the account itself (an eth-implicit NEAR account), and `rlp_execute` is its only entry point for executing user-signed Ethereum-style actions (base-token transfers, ERC-20 transfers, arbitrary function calls) on NEAR. Once `has_in_flight_tx` is stuck `true`, the account can never again execute any action through this interface — funds and access controlled exclusively through the eth-emulation path become permanently unusable/frozen, matching the "permanently frozen funds" impact category. This is a transaction-triggered, unrecoverable denial of the account's core functionality, reachable by any relayer or by the account owner themselves submitting a single crafted signed transaction.

### Likelihood Explanation
The trigger is a single RLP-encoded transaction/relayer call whose wrapped action specifies a large `gas` value relative to the gas attached to the outer `rlp_execute` call, taking the `ERC20Transfer` (or `EOABaseTokenTransfer` with `address_check`) path so that a `.then()` scheduling call inside the callback exceeds the receipt's remaining prepaid gas. This requires no validator or node compromise, no elevated privileges, and no protocol bug beyond the contract's own flawed invariant — only a specific combination of gas values in a normal signed transaction. The exact minimal gas values needed to force the failure *inside* the callback (as opposed to inside the initial `rlp_execute`, which the existing test shows is otherwise recoverable) were not verified end-to-end in this review; that would require executing the contract to pin down precise gas thresholds.

### Recommendation
Do not rely on a single boolean flag mutated by "run first, then do more work" callback code, since NEAR only persists a whole receipt's state atomically. Options:
- Reset `has_in_flight_tx = false` as the very last operation of every callback path, and ensure no further fallible/gas-sensitive work (like scheduling additional promises with externally-influenced gas amounts) can happen after clearing it in a way that risks rolling it back together with the reset.
- Add a bounded, permissionless "unstick" mechanism (e.g., after N blocks/some fixed gas-timeout with no resolution, allow the account owner to reset `has_in_flight_tx`), mirroring the "emergency exit" recommendation from the original report.
- Bound/validate `action.gas()` so it cannot push a downstream `.then()` scheduling call past the remaining gas budget of the callback receipt, avoiding the failure mode altogether.

### Proof of Concept
Conceptual PoC (not independently executed):
1. Deploy/target an eth-implicit account with the `near-wallet-contract`.
2. Craft and sign an RLP-encoded Ethereum transaction representing an `ERC20Transfer` (`EthEmulationKind::ERC20Transfer`) whose wrapped NEAR `FunctionCall` action specifies a very large `gas` value (close to `max_total_prepaid_gas`).
3. Submit this via `rlp_execute` attaching only enough gas to cover the initial `storage_balance_of` lookup plus a small margin — not enough to also cover `NEP_141_STORAGE_BALANCE_CALLBACK_GAS + action.gas()` for the `.then()` call inside `nep_141_storage_balance_callback`.
4. `rlp_execute` succeeds, sets `has_in_flight_tx = true`, and schedules `storage_balance_of` → `nep_141_storage_balance_callback`.
5. Inside the callback, `self.has_in_flight_tx = false` executes first, but the subsequent `.then(ext.rlp_execute_callback(...))` scheduling call exceeds the receipt's remaining gas and panics with `GasExceeded`.
6. The whole callback receipt fails and rolls back, discarding the `has_in_flight_tx = false` write; the previously committed `has_in_flight_tx = true` persists.
7. Any further call to `rlp_execute` now immediately returns `"Error: transaction already in progress, please try again later."` forever, as shown by the unconditional guard at [6](#0-5) .

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-128)
```rust
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );

        match result {
            Ok(promise) => {
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(e) => PromiseOrValue::Value(e.into()),
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-273)
```rust
    #[private]
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
        };
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L319-327)
```rust
    #[private]
    pub fn ban_relayer(&mut self) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        ExecuteResponse {
            success: false,
            success_value: None,
            error: Some("Error: faulty relayer".into()),
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-458)
```rust
        TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { receiver_id, .. }) => {
            // In the case of the emulated ERC-20 transfer, the receiving account
            // might not be registered with the NEP-141 contract (per the NEP-145)
            // storage standard. Therefore we must create a multi-step promise where
            // first we check if the receiver is registered and then if not call
            // `storage_deposit` in addition to `ft_transfer`.
            let token_id = target;
            let callback_gas = NEP_141_STORAGE_BALANCE_CALLBACK_GAS.saturating_add(action.gas());
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let storage_balance_args =
                format!(r#"{{"account_id": "{}"}}"#, receiver_id.as_str()).into_bytes();
            Promise::new(token_id.clone())
                .function_call(
                    "storage_balance_of".into(),
                    storage_balance_args,
                    NearToken::from_yoctonear(0),
                    NEP_141_STORAGE_BALANCE_OF_GAS,
                )
                .then(ext.nep_141_storage_balance_callback(
                    token_id,
                    receiver_id,
                    action,
                    caller_deposit,
                ))
        }
```

**File:** runtime/runtime/src/lib.rs (L1078-1088)
```rust
        // Committing or rolling back state.
        match &result.result {
            Ok(_) => {
                state_update.commit(StateChangeCause::ReceiptProcessing {
                    receipt_hash: receipt.get_hash(),
                });
            }
            Err(_) => {
                state_update.rollback();
            }
        };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L34-77)
```rust
#[tokio::test]
async fn test_insufficient_gas() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    // If not enough gas is attached to the `rlp_execute` call then the action fails.
    let target = "some.account.near".to_string();
    let action = Action::FunctionCall {
        receiver_id: target.clone(),
        method_name: "greet".into(),
        args: br#"{"name": "Aurora"}"#.to_vec(),
        gas: 5_000_000_000_000,
        yocto_near: 0,
    };
    let signed_transaction = utils::create_signed_transaction(
        0,
        &target.parse().unwrap(),
        Wei::zero(),
        action,
        &wallet_sk,
    );

    let error = wallet_contract
        .inner
        .call(crate::tests::RLP_EXECUTE)
        .args_json(serde_json::json!({
            "target": target,
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_gas::NearGas::from_tgas(7))
        .transact()
        .await
        .unwrap()
        .raw_bytes()
        .unwrap_err();

    assert!(
        error.to_string().contains("Exceeded the prepaid gas."),
        "Error should be that there was not enough gas"
    );

    // But the contract is still usable afterwards.
    utils::deploy_and_call_hello(&worker, &wallet_contract, &wallet_sk, 0).await?;

    Ok(())
```
