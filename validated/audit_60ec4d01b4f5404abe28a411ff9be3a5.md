## Title
Wallet Contract's hardcoded `ADDRESS_REGISTRAR_ACCOUNT_ID` is a build-time constant whose misconfiguration silently disables the anti-fund-redirection relayer check - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The reported bug class is a hardcoded, unverified oracle/registry address that is wrong, causing the contract to consume data from the wrong source and silently produce an incorrect (and exploitable) result. The NEAR Wallet Contract (the meta-transaction relay contract used for ETH-implicit accounts) contains a structurally identical pattern: the account ID of the "address registrar" used to detect fund-redirection attacks by a relayer is baked in at compile time from a plain text file, with no on-chain validation that it is the correct/canonical registrar contract.

### Finding Description
The Wallet Contract hard-codes the registrar account ID via `include_str!`: [1](#0-0) 

and later parses/uses it, with only a panic-on-parse-failure as validation (no check that it's the *correct* registrar, only that it's syntactically an `AccountId`): [2](#0-1) 

This registrar lookup is the sole security control that stops a malicious or lazy relayer from redirecting a user's intended transfer to a *named* registered account (e.g. `bob.near`) into a transfer to the raw eth-implicit sub-account instead. The callback logic bans the relayer only if the registrar reports the target address *is* registered: [3](#0-2) 

If the hardcoded `ADDRESS_REGISTRAR_ACCOUNT_ID` value points to the wrong account (analogous to `StableOracleWBTC` hardcoding the WETH/USD oracle instead of BTC/USD) — e.g., a stale, unrelated, or non-existent contract — the cross-contract `lookup` call will either fail (safe, but denial of service) or return `None` for every address, since the wrong contract will never contain the real registry mappings maintained by the genuine `AddressRegistrar` contract: [4](#0-3) 

In the `None` branch, `address_check_callback` treats the target as legitimately unregistered and lets the relayer's chosen promise proceed without banning it or rejecting the transaction: [5](#0-4) 

This completely defeats the intended protection documented at the top of `validate_tx_relayer_data`/`parse_rlp_tx_to_action`, whose entire purpose is to ensure a relayer routes funds to the true named account rather than the raw address-derived account: [6](#0-5) 

### Impact Explanation
If the compiled-in registrar account ID is wrong for a given deployment/chain (mirroring the exact "wrong hardcoded oracle address" root cause from the reference report), any relayer — a completely unprivileged party that merely forwards a signed meta-transaction on behalf of the Wallet Contract owner — can set `target` to the attacker-controlled eth-implicit account instead of the legitimate registered NEAR account name, and the contract will accept it as valid because the (broken) registrar lookup always resolves to "not registered." Since `action_to_promise` executes the transfer to whatever `target` was supplied, this results in unauthorized redirection of the user's funds away from their intended recipient, i.e., concrete unauthorized value movement, without the malicious relayer ever being banned or the transaction being rejected.

### Likelihood Explanation
Likelihood depends entirely on whether the hardcoded value shipped with a given Wallet Contract binary is correct for its deployment target; the mechanism (`std::include_str!` of a plain text file with no runtime verification against a canonical/derived address) provides no defense-in-depth if that file is ever wrong, stale, or mismatched between build and deployment (e.g., a redeployment of the registrar contract, or a build for the wrong network reusing another network's file). Any relayer processing `EOABaseTokenTransfer` transactions with `address_check: Some(_)` can trigger the vulnerable path; no special privileges are needed beyond acting as an (untrusted-by-design) relayer.

### Recommendation
Do not rely solely on a build-time hardcoded, unverified account ID for a security-critical lookup. At minimum, validate the registrar's identity/behavior on-chain (e.g., via a protocol-level constant tied to genesis config or a verifiable global contract mapping, analogous to `eth_wallet_global_contract_hash`), add monitoring/consistency checks between the deployed registrar and the compiled-in ID, and ensure build tooling asserts the correct per-chain value is embedded before a Wallet Contract binary is published for a given `chain_id`.

### Proof of Concept
1. Build/deploy a Wallet Contract binary where `ADDRESS_REGISTRAR_ACCOUNT_ID` (file at `runtime/near-wallet-contract/implementation/wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID`) does not point to the real `AddressRegistrar` contract used for that chain (e.g., points to an unrelated or empty contract).
2. A user's registered account `bob.near` registers its address via `AddressRegistrar::register`.
3. A user sends a `rlp_execute` ETH-emulated base-token transfer whose `tx.to` equals `account_id_to_address(bob.near)`.
4. A malicious relayer sets `target` to the raw eth-implicit account `0x<address>` (satisfying `to == target` validation in `validate_tx_relayer_data`) instead of `bob.near`.
5. `address_registrar.lookup(address)` (pointed at the wrong contract) returns `None` even though `bob.near` is genuinely registered.
6. `address_check_callback` proceeds to execute the transfer to `0x<address>` instead of banning the relayer, redirecting the user's funds away from `bob.near`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-192)
```rust
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
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
        let current_account_id = env::current_account_id();
        let promise = if maybe_account_id.is_some() {
            // We intentionally do not increment the nonce in this case because the
            // error is caused by a faulty relayer, not the user. An honest relayer
            // may still be able to successfully send the user's intended transaction.
            if env::signer_account_id() == current_account_id {
                create_ban_relayer_promise(current_account_id)
            } else {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
            }
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            match action_to_promise(target, action)
                .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
            {
                Ok(p) => p,
                Err(e) => {
                    return PromiseOrValue::Value(e.into());
                }
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-432)
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
        }
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L88-102)
```rust
    /// Attempt to look up the account ID associated with the given address.
    /// If an entry for that address is found then the associated account id
    /// is returned, otherwise `None` is returned. Use the `register` method
    /// to add entries to the map.
    /// This function will panic if the given address is not the hex-encoding
    /// of a 20-byte array. The `0x` prefix is optional.
    pub fn lookup(&self, address: String) -> Option<AccountId> {
        let address = {
            let mut buf = [0u8; 20];
            hex::decode_to_slice(address.strip_prefix("0x").unwrap_or(&address), &mut buf)
                .unwrap_or_else(|_| env::panic_str("Invalid hex encoding"));
            buf
        };
        self.addresses.get(&address).cloned()
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L66-81)
```rust
    // The way an honest relayer assigns `target` is as follows:
    // 1. If the Ethereum transaction payload represents a Near action then use the receiver_id,
    // 2. If the payload looks like a supported Ethereum emulation then use the address registrar:
    // 2.a. if the tx.to address is registered then use the associated account id,
    // 2.b. otherwise, tx.to == target
    // 3. Otherwise, tx.to == target
    // Given this algorithm, the only way to have `TargetKind::EthImplicit` is in the
    // following cases:
    // I)   The Ethereum transaction payload is not parseable as a known action,
    // II)  The payload is parsable as a Near action and the receiver_id is an eth-implicit account
    // III) The payload is parsable as a supported Ethereum emulation but the to address is
    //      not registered in the address registrar.
    // Therefore, to determine if the relayer is honest we must always parse the payload and
    // we only need to check the registrar if the payload is parseable as an Ethereum emulation.
    // Note: the `TargetKind` is determined in `validate_tx_relayer_data` above, and that function
    // also confirms that the `target` is compatible with the user's `tx.to`.
```
