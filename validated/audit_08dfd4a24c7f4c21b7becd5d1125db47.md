### Title
Hardcoded, per-network-baked `ADDRESS_REGISTRAR_ACCOUNT_ID` in the Wallet Contract can permanently disable the address-collision safety check for ETH-implicit account transfers - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract (deployed as a shared **global contract** for all ETH-implicit accounts) resolves the Address Registrar it queries via a value baked into the WASM at compile time with `std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID")`, rather than via a value that can be set/validated per-deployment. This mirrors the Sherlock/Sense `RollerUtils` bug: a security-critical external contract address is a hardcoded constant instead of a validated, settable reference, and it is baked separately per network (`mainnet.wasm`, `testnet.wasm`, `localnet.wasm`), each with its own registrar account ID and chain ID files. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ADDRESS_REGISTRAR_ACCOUNT_ID` is loaded from a source-tree file at compile time and used, unvalidated beyond basic `AccountId` syntax, to build the cross-contract call target for the address-collision safety check performed during `rlp_execute`: [4](#0-3) 

This check exists specifically to prevent a faulty/malicious relayer from silently redirecting a "base token transfer" that was meant for a *named* Near account to that account's *derived ETH-implicit shadow address* instead — i.e., it protects the user's transfer intent when the transaction target is `TargetKind::EthImplicit`: [5](#0-4) 

Because the Wallet Contract WASM is deployed once as an immutable global contract, referenced by a fixed `CryptoHash` for the whole network (mainnet/testnet each have their own hard-coded hash and their own baked-in registrar id and chain id): [6](#0-5) 

any mismatch between the compiled-in `ADDRESS_REGISTRAR_ACCOUNT_ID` and the actually-deployed Address Registrar contract for that chain (wrong id baked in at build time, or the registrar being redeployed/migrated to a new account after the wallet contract is already live) makes the cross-contract `lookup` call target a non-existent or wrong contract. The registrar `lookup` call would then fail (`PromiseResult::Failed`), and `address_check_callback` returns a failure response: [7](#0-6) 

This is structurally the same root cause as the Sherlock finding: a security-relevant external contract address is hardcoded as a hand-maintained constant instead of being verified/settable, with no on-chain mechanism to correct it once the referencing contract is deployed (the Sherlock fix moved to constructor-injection with validation; here the equivalent fix would be an on-chain settable/validated registrar reference, which does not exist — the value can only be changed by shipping an entirely new global contract binary and having every account's global-contract hash reference updated, a protocol-level migration).

### Impact Explanation
Because the Wallet Contract is the sole binary governing every ETH-implicit account's execution logic for a given chain, an incorrect baked-in registrar id would deterministically and permanently disable the "address collision" safety check (`EOABaseTokenTransfer{ address_check: Some(_) }` path) for *all* ETH-implicit accounts on that network simultaneously, for every transaction whose `to` corresponds to another wallet-contract's shadow address that is unregistered/mis-registered. Unlike the Solidity case (a synchronous revert that hard-bricks `cooldown()`), the NEAR async promise model degrades this gracefully to `success: false` per affected call rather than reverting the whole chain of promises, so it does not directly move value or corrupt other unrelated wallet functionality; regular NEAR-native actions, self-transfers, and non-address-checked EOA transfers remain unaffected. Given the report's strict acceptance bar (concrete unauthorized value movement, fee/gas bypass, state-root divergence, receipt loss, permanently frozen funds, or transaction-triggered halt), this analog only reaches a "feature permanently broken/unfixable without a network-wide contract migration" outcome for a specific relayer-honesty safety net — it does not itself cause fund loss, double execution, or state divergence, because the affected calls simply fail their promise and no balance transfer occurs.

### Likelihood Explanation
Low-to-moderate. It requires a build/deployment-time misconfiguration (wrong `ADDRESS_REGISTRAR_ACCOUNT_ID` baked into the network-specific WASM, e.g. testnet value baked into a mainnet build, mirroring how the Sense team baked in a Goerli address) or a post-deployment migration of the Address Registrar contract to a new account without a corresponding wallet-contract redeploy/migration. The repo's own test harness (`test_context.rs`) explicitly rewrites this file at build time per test run, underscoring that the value is deployment-specific and manually managed rather than derived/verified on-chain, which is exactly the maintenance hazard the Sherlock report warned about. [8](#0-7) 

### Recommendation
- Do not rely solely on a compile-time-included file for a security-relevant external contract address referenced by an immutable, globally-shared contract. At minimum, validate at first use (or in tests/CI) that the compiled-in `ADDRESS_REGISTRAR_ACCOUNT_ID` matches an actually-deployed registrar contract on the target network before shipping the corresponding `wallet_contract_*.wasm`/global contract hash.
- Consider making the registrar reference resolvable/updatable independent of the wallet contract's immutable global-contract hash (e.g., via a well-known lookup indirection contract), so a misconfiguration or future migration does not require re-deploying and re-hashing the entire global Wallet Contract for every ETH-implicit account on the network.
- Add a CI check that cross-references `ADDRESS_REGISTRAR_ACCOUNT_ID`/`CHAIN_ID` per-network build artifacts against the actually deployed registrar/chain configuration to catch mismatches before release, mirroring the "validate the input address" recommendation from the Sherlock report.

### Proof of Concept
Not independently reproducible as a state-transition exploit from the current code alone (no test in-repo currently demonstrates the misconfigured-registrar scenario). The mechanism is established by the code paths cited above: (1) `ADDRESS_REGISTRAR_ACCOUNT_ID` is compiled in per network with no on-chain validation [1](#0-0) ; (2) it is used directly as the cross-contract call target for the collision-safety check [9](#0-8) ; (3) the wallet contract is deployed once as an immutable, network-wide global contract identified by a fixed hash [10](#0-9) . Given these, if the baked-in id is wrong for the network it is deployed to, every `rlp_execute` transaction hitting the `EOABaseTokenTransfer{address_check: Some(_)}` branch will fail its registrar lookup and return `success: false` from `address_check_callback`, permanently (until a new global contract is shipped and adopted).

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

**File:** runtime/near-wallet-contract/src/lib.rs (L82-105)
```rust
/// Returns the global contract hash for the ETH wallet contract on a given chain.
/// This is the hash of the deployed global contract that ETH implicit accounts
/// should use when the EthImplicitGlobalContract protocol feature is enabled.
///
/// For other chains (localnet, test chains): Uses the hash of the embedded
/// wallet contract WASM, allowing tests to deploy the same contract as a
/// global contract.
pub fn eth_wallet_global_contract_hash(chain_id: &str) -> CryptoHash {
    match chain_id {
        // 2zodJZK2e4nnv5AqwCRnenNSmkikXhEd7PPY6BmfTmW4
        chains::MAINNET | chains::MOCKNET => CryptoHash([
            0x1d, 0xaa, 0x83, 0x5c, 0x46, 0x37, 0xf7, 0xae, 0x3d, 0x92, 0x40, 0x95, 0xba, 0x3f,
            0x0b, 0xf2, 0x82, 0x9b, 0xcf, 0xa1, 0x7b, 0x10, 0x68, 0xcd, 0x58, 0xbd, 0x85, 0x3d,
            0xca, 0xd7, 0xce, 0xb5,
        ]),
        // 3PpYvRxBfC5BkZxTw8ZFG3D52w1ZRhvDDWirKoxphMDn
        chains::TESTNET => CryptoHash([
            0x23, 0x8f, 0xea, 0xc1, 0xf8, 0x6c, 0xc9, 0xf9, 0xf4, 0x00, 0x3e, 0x3f, 0x6d, 0x5a,
            0xeb, 0xc0, 0x4e, 0xae, 0xa9, 0xc3, 0x94, 0x03, 0x2b, 0xd2, 0x94, 0x70, 0xe9, 0x60,
            0x9b, 0x67, 0xf6, 0xc5,
        ]),
        _ => *LOCALNET.read_contract().hash(),
    }
}
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/utils/test_context.rs (L175-188)
```rust
    async fn deploy_address_registrar(worker: &Worker<Sandbox>) -> anyhow::Result<Contract> {
        let base_dir = Path::new(BASE_DIR).parent().unwrap().join("address-registrar");
        let contract_bytes = build_contract(base_dir, "eth-address-registrar").await?;
        let contract = worker.dev_deploy(&contract_bytes).await?;

        // Initialize the contract
        contract.call("new").transact().await.unwrap().into_result().unwrap();

        // Update the file where the Wallet Contract gets the address registrar account id from
        tokio::fs::write(address_registrar_account_id_path(BASE_DIR), contract.id().as_bytes())
            .await?;

        Ok(contract)
    }
```
