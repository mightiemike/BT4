## Title
Hardcoded `ADDRESS_REGISTRAR_ACCOUNT_ID` baked into the compiled Wallet Contract causes permanent DoS of ETH-emulated transfers if the registrar is ever redeployed - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Wallet Contract (NEP-518, deployed as a global contract for all ETH-implicit accounts) resolves the account ID of the address-registrar dependency from a value baked into the compiled WASM at build time, rather than from any updatable, on-chain-configurable source. If the registrar contract is ever redeployed under a different account, every ETH-implicit wallet instance on the network permanently loses the ability to perform address-checked base-token transfers, since the wasm binary (and the global contract code hash it is identified by) cannot be changed for already-deployed accounts without a network-wide protocol/global-contract upgrade.

### Finding Description
`ADDRESS_REGISTRAR_ACCOUNT_ID` is compiled into the contract as a constant string via `include_str!`, exactly analogous to hardcoding an external dependency address at construction time in the original report: [1](#0-0) 

This constant is parsed into an `AccountId` and used to build the promise that queries the registrar every time an ETH-emulated transaction targets another eth-implicit account whose address is not already known to be a named account: [2](#0-1) 

The `address_check` path is triggered whenever a user submits an RLP-encoded transaction (via `rlp_execute`) whose `to` address maps to another eth-implicit target that isn't already registered, which is a normal path reachable by any relayer/transaction signer: [3](#0-2) 

If the promise to the registrar account fails (e.g., because that account no longer holds the registrar, was migrated, or never existed at the hardcoded ID on a particular deployment), `address_check_callback` unconditionally returns a failure and does not attempt any alternative resolution: [4](#0-3) 

Unlike a per-account configurable value, this identifier is embedded in the WASM bytes that are shared as a single global contract across all ETH-implicit accounts on a given chain (`MAINNET`/`TESTNET`/`LOCALNET`/`OLD_TESTNET` in `near-wallet-contract`): [5](#0-4) 

Because it is compiled in and shipped as a global contract code hash resolved at runtime from the account's contract identifier, there is no mechanism to migrate every already-deployed wallet instance to a new registrar account without a coordinated protocol/global-contract-hash upgrade — the same structural weakness flagged in the original `asdRouter.sol` finding for the `noteAddress`.

### Impact Explanation
If the address registrar is ever redeployed to a new account (operationally plausible, as it is a separate, independently-deployed contract referenced by account ID rather than derived deterministically), every eth-implicit wallet contract instance across the network will have its `address_check` calls permanently fail. This causes a persistent denial of service for a specific, legitimate user operation class — ETH-emulated base-token transfers where the recipient's Near account name is not already known to the relayer — for every ETH-implicit account, until a chain-wide global-contract code upgrade is rolled out. This matches a medium-severity "DOS due to a hardcoded external dependency address" class, consistent with the judged severity in the original report.

### Likelihood Explanation
Low but non-zero: this requires the address-registrar to be redeployed at a new account ID after the Wallet Contract has already been deployed/adopted as a global contract. This is an operational/administrative event rather than something exploitable by a malicious actor in isolation, but it is a reachable, unprivileged code path (any relayer/tx signer invoking `rlp_execute` for a transfer to an unregistered eth-implicit target) that becomes permanently broken as a result.

### Recommendation
Avoid baking a mutable dependency's account ID into the WASM binary as a static constant. Instead, resolve the address registrar dynamically (e.g., via a well-known, protocol-level deterministic account, or a value stored in on-chain state that can be updated through a governed upgrade path) so that a migration of the registrar does not require a hard-fork-style redeployment of the global contract to restore functionality for already-created ETH-implicit accounts.

### Proof of Concept
1. Wallet Contract A (ETH-implicit account) is created and uses the global contract code that hardcodes `ADDRESS_REGISTRAR_ACCOUNT_ID = "address-map.near"`.
2. A relayer submits an RLP-encoded transaction via `rlp_execute` whose `to` field targets another eth-implicit account B not yet registered as a named account; this produces `TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer { address_check: Some(address), .. })`.
3. `inner_rlp_execute` builds a promise to `address-map.near.lookup(address)`.
4. If `address-map.near` no longer hosts the registrar (e.g., migrated to `address-map-v2.near`), the promise fails, and `address_check_callback` returns `ExecuteResponse { success: false, error: Some("Call to Address Registrar contract failed") }`.
5. This failure is permanent for every wallet contract instance sharing this global contract code hash, and can only be fixed by shipping a new global contract binary and having every account opt into it — a network-wide coordinated fix, not a simple state update.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L107-121)
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
```

**File:** runtime/near-wallet-contract/src/lib.rs (L6-20)
```rust
static MAINNET: WalletContract =
    WalletContract::new(include_bytes!("../res/wallet_contract_mainnet.wasm"));

static TESTNET: WalletContract =
    WalletContract::new(include_bytes!("../res/wallet_contract_testnet.wasm"));

/// Initial version of WalletContract. It was released to testnet, but not mainnet.
/// We still use this one on testnet protocol version 70 for consistency.
/// Example account:
/// https://testnet.nearblocks.io/address/0xcc5a584f545b2ca3ebacc1346556d1f5b82b8fc6
static OLD_TESTNET: WalletContract =
    WalletContract::new(include_bytes!("../res/wallet_contract_testnet_pv70.wasm"));

static LOCALNET: WalletContract =
    WalletContract::new(include_bytes!("../res/wallet_contract_localnet.wasm"));
```
