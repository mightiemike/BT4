Based on my research, I found a concrete structural analog to the "fake token" pattern in the Sherlock report, located in the **NEAR Wallet Contract**'s Ethereum ERC-20 emulation logic.

### Title
Relayer-controlled `target` account is used as the NEP-141 token contract for `ft_transfer`/`storage_balance_of` calls without being cryptographically bound to the signed Ethereum transaction's `to` address for the ERC20Transfer path - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs`, `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`)

### Summary
`rlp_execute` takes a caller/relayer-supplied `target: AccountId` and a signed Ethereum transaction, and, when the transaction data decodes as an ERC-20 `transfer(to, value)` call, it builds a `FunctionCall` action pointed at `target` invoking `ft_transfer`/`storage_balance_of` [1](#0-0) . The relayer, not the wallet owner, chooses `target`; the contract's own comment states this is only "the way an *honest* relayer" assigns it [2](#0-1) .

### Finding Description
The wallet-contract owner signs an Ethereum-style transaction where only the *address* (`tx.to`) of the intended token contract is available (Ethereum has no named accounts). Mapping that address to an actual NEAR account id (the real NEP-141 token contract) is supposed to happen via the address registrar. The code explicitly implements an on-chain verification path for this mapping only for the base-token-transfer / `EthImplicit` case: when `target_kind` resolves to `TargetKind::EthImplicit(address)`, the contract schedules a registrar lookup and an `address_check_callback` before proceeding [3](#0-2) .

For the ERC-20 `transfer` emulation path, however, once `target_kind` is *not* `EthImplicit` (i.e., the relayer claims the registrar already resolved `tx.to` to `target`), the contract takes `target` at face value as `token_id` and immediately issues `storage_balance_of` / `ft_transfer` calls to it, with no on-chain callback verifying that this `target` account is actually the NEP-141 contract registered for the `tx.to` Ethereum address used in the user's signature [4](#0-3) . This is structurally identical to `StreamFactory.createStream` trusting a caller-supplied token address without validating it: here the wallet trusts a caller-supplied `target` account id as "the token contract" without validating it against the registrar-bound ground truth for the ERC20Transfer branch.

### Impact Explanation
A malicious/dishonest relayer that calls `rlp_execute` can supply an attacker-controlled `target` account (a fake contract exposing `ft_transfer`/`storage_balance_of` that trivially return success) instead of the real registered NEP-141 token contract implied by the user's signed `tx.to`. Because the wallet contract computes and refunds the relayer's gas `fee` based on the Ethereum transaction's gas parameters regardless of whether the intended real-token transfer actually occurred against the genuine token contract [5](#0-4) , a relayer can present the transaction as executed successfully (invoking its own fake token contract instead of the real one), collect its NEAR gas-refund fee from the wallet, while the user's real token transfer never took place — a value-movement/fee-bypass primitive reachable purely through the relayer-controlled `target` parameter of a single `rlp_execute` call.

### Likelihood Explanation
`rlp_execute` is a `#[payable]`, publicly callable method reachable by any transaction signer/RPC caller who is willing to act as (or collude with) the relayer; `target` is a plain function argument, not derived on-chain from `tx.to` for every code path. The only defense (`address_check_callback`) is wired for the `EthImplicit` target-kind branch, and the ERC20Transfer branch is reached specifically when `target_kind` is *not* `EthImplicit`, i.e., precisely the case the equivalent check is skipped in the promise chain shown above.

### Recommendation
Before dispatching `ft_transfer`/`storage_balance_of` to `target` in the `ERC20Transfer` case, perform the same registrar-lookup-and-verify callback pattern already used for `EOABaseTokenTransfer` with `address_check: Some(address)`, so `target` is provably the NEP-141 contract the address registrar associates with the `tx.to` address signed by the user, not merely whatever account id the relayer supplies.

### Proof of Concept
1. Wallet owner signs an Ethereum tx: `to = <address of legit token T>`, data = ERC20 `transfer(victim_or_self, amount)`.
2. Malicious relayer calls `rlp_execute(target = <attacker's fake NEP-141 contract F>, tx_bytes_b64 = <signed tx>)`.
3. `parse_rlp_tx_to_action` classifies this as `ParsableTransactionKind::EthEmulation(ERC20Transfer)`; since the relayer asserts `target=F` (not an `EthImplicit` derivation of `tx.to`), the `TargetKind::EthImplicit` guard in `lib.rs:107-121` is skipped and `TransactionKind::EthEmulation(eth_emulation.into())` (`ERC20Transfer`) is kept as-is.
4. `inner_rlp_execute`'s dispatch calls `Promise::new(F).function_call("storage_balance_of", ...).then(... .nep_141_storage_balance_callback(F, receiver_id, action, caller_deposit))` — `F` is used directly as `token_id`, with no verification against the registrar entry for the original `tx.to` address [4](#0-3) .
5. `F` (attacker-controlled) returns whatever the attacker wants (e.g., always "success"), the wallet contract still refunds the relayer's precomputed `fee` from `tx.max_fee_per_gas * tx.gas_limit`, while the real token `T` balance is untouched.

**Note on completeness:** I was unable to view the full body of `validate_tx_relayer_data` (only its signature and surrounding comments were retrieved) due to running out of tool-call iterations, so I cannot fully rule out that some additional upstream check constrains `target` for the non-`EthImplicit` case beyond what is shown. This should be verified directly in `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs` before treating this as fully confirmed.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs (L59-93)
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
        }
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L66-77)
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
