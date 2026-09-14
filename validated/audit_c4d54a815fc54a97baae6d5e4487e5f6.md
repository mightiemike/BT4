### Title
Wallet Contract Hardcodes an Unverified `ADDRESS_REGISTRAR_ACCOUNT_ID` Oracle That Can Be Squatted/Malicious, Enabling Relayer-Key Deletion and Transaction Denial - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Wallet Contract (the shared/global contract deployed on **every** ETH-implicit account on NEAR, per `EthImplicitGlobalContract`) hardcodes the account ID of the `AddressRegistrar` contract it trusts for base-token-transfer address checks. This value is baked into the WASM at compile time from a plain-text file, with no on-chain verification that the referenced account is protocol-owned, immutable, or even deployed. Because the corresponding `AddressRegistrar::register` method is fully permissionless and grants the mapping to whichever account calls first, this hardcoded dependency is a single, unauthenticated trust anchor used by the on-chain logic of every ETH-implicit account.

### Finding Description
`ADDRESS_REGISTRAR_ACCOUNT_ID` is compiled directly from a static file into the contract: [1](#0-0) 

with the current value: [2](#0-1) 

This mirrors the analog bug class exactly: a critical external dependency (address) is hardcoded into contract logic without any mechanism to configure, verify ownership of, or reconfigure it post-deployment — just as `StableOracleDAI` hardcoded an incorrect oracle address with no constructor parameter to fix it.

At runtime, this constant is parsed and dialed unconditionally whenever an EOA base-token transfer targets what looks like another ETH-implicit address: [3](#0-2) 

The response from this hardcoded account is treated as ground truth in `address_check_callback`: [4](#0-3) 

If `promise_result` indicates the queried address maps to an existing named account (`Some(account_id)`), and the transaction was self-signed (`signer_account_id() == current_account_id`, the normal case where a relayer holds a `FunctionCall` access key on the user's wallet), the wallet contract calls `create_ban_relayer_promise`, which **deletes the signer's own access key**: [5](#0-4) 

The `AddressRegistrar` contract that is supposed to back this oracle is itself unauthenticated — `register` lets *any* caller register *any* `account_id`, with only "first writer wins" collision protection, and nothing ties the registrant to ownership of the account being registered: [6](#0-5) 

Because the trust anchor (the account name) is fixed at compile time into a contract binary shared by every ETH-implicit account, whichever party creates and controls that named account (e.g., if it is not pre-created/pinned by protocol governance at genesis, or is created before the intended owner on a given deployment) becomes the sole arbiter of the address→account oracle for the entire network's Wallet Contract users, with no way to migrate away from it since the value cannot be changed without redeploying/upgrading the global contract binary.

### Impact Explanation
An attacker who controls the account referenced by `ADDRESS_REGISTRAR_ACCOUNT_ID` (by squatting it before the intended owner, or if it is otherwise not securely pinned) can:
- Return `Some(<arbitrary_account>)` for arbitrary queried addresses, causing the Wallet Contract to treat legitimate relayer-submitted, self-signed transactions as "faulty," triggering `create_ban_relayer_promise` and **deleting the signer's own access key** on the victim's ETH-implicit account (`lib.rs:166` → `lib.rs:503-512`) — a denial-of-service / loss-of-access impact reachable purely from an unprivileged relayer/meta-transaction flow.
- Return `Failed` (e.g., by simply not deploying the expected contract, or by making a call that panics) causing the whole address-check pathway to abort with an error (`lib.rs:142-148`), denying that class of cross-contract transfers network-wide for any chain build where the baked-in value was misconfigured.

This falls under "transaction-triggered halt"/"permanently frozen funds" impact categories: an unprivileged actor's transaction (or a malicious relayer response chain triggered by processing a normal user transaction) can permanently revoke the user's own relayer access key, freezing their ability to transact via that relayer path.

### Likelihood Explanation
Reachable purely through the standard `rlp_execute` flow available to any relayer/meta-transaction sender interacting with any ETH-implicit account — no privileged role is required. The only prerequisite is control (or malicious deployment) of the specific account name baked into the shared Wallet Contract binary, which is a compile-time constant with no on-chain enforcement of ownership, ADDRESS_REGISTRAR permission checks, or a fallback/verification path.

### Recommendation
- Do not bake a single, unauthenticated account ID into the globally-shared Wallet Contract binary as a security-critical oracle. Instead, make the registrar address either immutable-and-provably-owned by protocol governance at genesis (with explicit validation at genesis/initialization time that it exists and is controlled as expected), or make it a configurable/versioned parameter that can be safely rotated.
- Add authentication to `AddressRegistrar::register` (e.g., require `predecessor_account_id == account_id`, or an access-key-based authorization) so only the actual owner of `account_id` can register its address mapping.
- Treat the registrar as untrusted in `address_check_callback`: do not delete a user's access key based solely on an oracle response, especially for the case where the relayer used the raw address; consider requiring additional confirmation or making the "ban" action reversible/appealable rather than unilateral key deletion.

### Proof of Concept
1. Deploy the Wallet Contract as a global contract with the baked-in `ADDRESS_REGISTRAR_ACCOUNT_ID` value.
2. As an unprivileged actor, before the legitimate registrar deployment happens (or on any network/build where that account is uncreated), create that account and deploy a malicious contract exposing `lookup(address) -> Option<AccountId>` that always returns `Some("attacker.near")`.
3. Have any relayer submit a self-signed `rlp_execute` EOA base-token transfer where the parsed target resolves to `TargetKind::EthImplicit` (`internal.rs` `parse_target`, lines 221-232) with `address_check: Some(address)` (`internal.rs:412-431`).
4. The Wallet Contract calls the attacker-controlled registrar, receives `Some(...)`, and since `env::signer_account_id() == current_account_id`, invokes `create_ban_relayer_promise`, deleting the user's own access key (`lib.rs:161-166`, `503-512`) — demonstrating unauthorized, attacker-triggered loss of the victim's wallet access via a hardcoded, unauthenticated dependency.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-192)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L503-512)
```rust
fn create_ban_relayer_promise(current_account_id: AccountId) -> Promise {
    let pk = env::signer_account_pk();
    Promise::new(current_account_id).delete_key(pk).function_call_weight(
        "ban_relayer".into(),
        Vec::new(),
        NearToken::from_yoctonear(0),
        Gas::from_tgas(1),
        GasWeight(1),
    )
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID (L1-1)
```text
address-map.near
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L36-86)
```rust
    #[payable]
    pub fn register(&mut self, account_id: AccountId) -> Option<String> {
        // It is not allowed to register eth-implicit accounts because the purpose
        // of the registry is to allow looking up the named account associated with
        // an address obtained via hashing, but eth-implicit accounts are already
        // parsable as addresses.
        if is_eth_implicit(&account_id) {
            let log_message = format!("Refuse to register eth-implicit account {account_id}");
            env::log_str(&log_message);
            return None;
        }

        // Must store the address and the account id
        let bytes_to_store = 20 + (account_id.len() as u128);
        let required_deposit =
            NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
        let given_deposit = env::attached_deposit();
        // The caller must pay for the storage cost of registering.
        if given_deposit < required_deposit {
            let message = format!(
                "Insufficient deposit to cover storage cost. Given={} Expected={}",
                given_deposit.as_yoctonear(),
                required_deposit.as_yoctonear(),
            );
            env::panic_str(&message);
        }

        let address = account_id_to_address(&account_id);

        match self.addresses.entry(address) {
            Entry::Vacant(entry) => {
                let address = format!("0x{}", hex::encode(address));
                let log_message = format!("Added entry {} -> {}", address, account_id);
                entry.insert(account_id);
                env::log_str(&log_message);
                Some(address)
            }
            Entry::Occupied(entry) => {
                let log_message = format!(
                    "Address collision between {} and {}. Keeping the former.",
                    entry.get(),
                    account_id
                );
                env::log_str(&log_message);
                // Transfer the deposit back to the caller since no storage was updated.
                let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
                env::promise_batch_action_transfer(refund_promise, given_deposit);
                None
            }
        }
    }
```
