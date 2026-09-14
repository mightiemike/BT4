## Title
Wallet Contract ERC-20 emulation resolves the `to` address to a synthetic ETH-implicit account without an Address Registrar lookup, causing NEP-141 transfers to a registered named account to be misdirected — ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs])

### Summary
The Wallet Contract (NEP-518, implementing the eth-implicit-account "EOA-on-NEAR" flow) deliberately performs an `AddressRegistrar` lookup before treating a base-token (`$NEAR`) transfer target address as a synthetic eth-implicit account, precisely to avoid sending funds to the wrong place when the address actually corresponds to a *named* (already-registered) NEAR account. However, the parallel ERC-20 (NEP-141) `transfer(to, value)` emulation path skips that check entirely and unconditionally derives the receiver as the raw hex-address account id, which can silently divert token transfers away from the actual intended, registered recipient.

### Finding Description
In `try_emulation` for `ERC20_TRANSFER_SELECTOR`, the receiver is computed purely by string formatting the 20-byte `to` address into an eth-implicit-style account id, with no registrar consultation: [1](#0-0) 

Compare this to the base-token transfer path in `internal.rs`, which explicitly notes that a target address might correspond to a registered *named* account and therefore requires confirming via the `AddressRegistrar` before assuming it is a bare eth-implicit account: [2](#0-1) 

The registrar-check flow is wired up in `lib.rs` only for `EOABaseTokenTransfer { address_check: Some(address), .. }`, calling `address_registrar.lookup(...)` before finalizing the transfer: [3](#0-2) 

But the `ERC20Transfer { receiver_id, .. }` branch only checks NEP-145 storage registration on the token contract (`storage_balance_of`) — it never asks the `AddressRegistrar` whether the address actually maps to some other named NEAR account: [4](#0-3) 

The `AddressRegistrar` itself exists precisely to let any named NEAR account (e.g. an exchange, contract, or user account) register the ETH-style address that corresponds to it (`keccak256(account_id)[12:32]`), so that ETH tooling addressing NEAR by 20-byte address can reach it: [5](#0-4) 

Because the ERC-20 transfer emulation ignores this registry, an ETH-tooling user who intends to send NEP-141 tokens to a real, registered named account (by supplying that account's registered address as `to`) will instead have their tokens routed via `ft_transfer` to the synthetic `0x{hex(to)}{suffix}` eth-implicit account id — a distinct NEAR account from the one the sender intended, which the intended recipient does not control unless it happens to also be the corresponding eth-implicit account (extremely unlikely, analogous to the original report's "loss of funds unless nonces coincidentally matched between chains").

### Impact Explanation
Any relayer/user submitting an Ethereum-emulated ERC-20 `transfer` call whose `to` argument is a registered address for a named NEAR account will have the NEP-141 tokens sent to an unrelated, synthetic `0x...` eth-implicit account instead of the intended named account. Since eth-implicit accounts require the corresponding Secp256K1 private key to control them, and the actual named account owner has no such key for that particular hash, the transferred tokens become effectively unrecoverable by the intended recipient — concrete unauthorized/misdirected value movement and permanent loss of user funds, matching the "Medium" severity class of the source report (loss of bridged/transferred assets due to unchecked/implicit receiver derivation).

### Likelihood Explanation
This is reachable by any ordinary user of the Wallet Contract (an EOA-style caller through the standard `rlp_execute` relayer flow) constructing a normal ERC-20 `transfer` call — no privileged access, malicious validator, or network-level compromise is required. The only precondition is that the token recipient has previously registered their real account address with the `AddressRegistrar` (the documented/intended way to reach named accounts from Ethereum tooling), which is an expected, encouraged usage pattern of the system.

### Recommendation
Mirror the `EOABaseTokenTransfer` pattern for `ERC20Transfer`: before finalizing the `ft_transfer` call, perform an `AddressRegistrar.lookup(to)` and, if a named account is found, redirect `receiver_id` to that registered account instead of the synthetic eth-implicit id (and consider banning/flagging a lazy relayer analogous to `address_check_callback`'s handling for base-token transfers).

### Proof of Concept
1. Named NEAR account `alice.near` calls `AddressRegistrar::register("alice.near")`, obtaining `address_A = 0xdead...beef`, per `runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs`.
2. A Wallet-Contract-controlled ETH-implicit account holds NEP-141 tokens and wants to send them to `alice.near`. The user, using standard Ethereum tooling, signs an ERC-20 `transfer(address_A, amount)` call targeting the token contract, since ETH tooling addresses `alice.near` via `address_A`.
3. The relayer submits this via `rlp_execute`; `eth_emulation::try_emulation` matches `ERC20_TRANSFER_SELECTOR` and computes `receiver_id = "0x" + hex(address_A) + suffix` — i.e., the synthetic eth-implicit account, NOT `alice.near`.
4. `lib.rs`'s `ERC20Transfer` handling only checks `storage_balance_of` for that synthetic account (registering it for storage if needed) and calls `ft_transfer` to it.
5. Tokens land in the synthetic `0x...` eth-implicit account, which `alice.near` cannot access (she does not hold the Secp256K1 key whose keccak-derived hash equals `address_A` by construction — that hash was derived from her account id string, not from any key she holds), resulting in permanent loss of the transferred NEP-141 tokens.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs (L59-92)
```rust
        ERC20_TRANSFER_SELECTOR => {
            // We intentionally map to `u128` instead of `U256` because the NEP-141 standard
            // is to use u128.
            let (to, value): (Address, u128) =
                ethabi_utils::abi_decode(&ERC20_TRANSFER_SIGNATURE, &tx.data[4..])?;
            let receiver_id: AccountId = format!("0x{}{}", hex::encode(to), suffix)
                .parse()
                .unwrap_or_else(|_| env::panic_str("eth-implicit accounts are valid account ids"));

            // Include any data after the main args as a memo in the transfer.
            // The main data takes 68 bytes because there is a 4-byte selector followed
            // by two arguments which are each allocated 32 bytes according to the
            // Solidity ABI standard.
            let memo = if tx.data.len() > 68 {
                Some(format!(r#""0x{}""#, hex::encode(&tx.data[68..])))
            } else {
                None
            };
            let args = format!(
                r#"{{"receiver_id": "{}", "amount": "{}", "memo": {}}}"#,
                receiver_id.as_str(),
                value,
                memo.as_deref().unwrap_or("null"),
            );
            Ok((
                Action::FunctionCall {
                    receiver_id: target.to_string(),
                    method_name: "ft_transfer".into(),
                    args: args.into_bytes(),
                    gas: 2 * FIVE_TERA_GAS,
                    yocto_near: 1,
                },
                ParsableEthEmulationKind::ERC20Transfer { receiver_id, fee },
            ))
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L104-122)
```rust
                (action, TransactionKind::NearNativeAction)
            }
        }
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

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L30-86)
```rust
    /// Computes the address associated with the given `account_id` and
    /// attempts to store the mapping `address -> account_id`. If there is
    /// a collision where the given `account_id` has the same address as a
    /// previously registered one then the mapping is NOT updated and `None`
    /// is returned. Otherwise, the mapping is stored and the address is
    /// returned as a hex-encoded string with `0x` prefix.
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
