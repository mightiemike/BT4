### Title
Hardcoded mainnet-only address registrar account embedded in all Wallet Contract network builds (testnet/localnet) - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID])

### Summary
The `near-wallet-contract` (the ETH-implicit account "Wallet Contract" that lets an Ethereum-style key sign NEAR actions via a relayer) hardcodes the address-registrar account it queries from a single source file, `ADDRESS_REGISTRAR_ACCOUNT_ID`, containing `address-map.near` [1](#0-0) . This value is `include_str!`-ed directly into the contract binary [2](#0-1) . Unlike the `CHAIN_ID` file, which the build script explicitly overwrites for each of the three network variants it compiles (`mainnet`, `testnet`, `localnet`) before invoking the dockerized build [3](#0-2) [4](#0-3) , the `ADDRESS_REGISTRAR_ACCOUNT_ID` file is never rewritten per target. This means the same mainnet-only account id `address-map.near` is baked into the `wallet_contract_testnet` and `wallet_contract_localnet` WASM artifacts as well, mirroring the reported bug class of a single hardcoded external-reference address being reused incorrectly across differing environments (e.g. reusing a wrong chain's oracle feed address).

### Finding Description
The registrar lookup is used to detect a "lazy/faulty relayer": when an Ethereum-emulated transaction targets another eth-implicit account, the contract calls the address registrar to check whether that address actually corresponds to a *named* NEAR account, in which case the relayer should have targeted the named account instead [5](#0-4) . The lookup call and its callback are: [6](#0-5) [7](#0-6) 

On testnet/localnet, `address-map.near` is not the actual deployed registrar for that network (it is either a nonexistent account, or an unrelated account someone else controls on that network's namespace). Consequently:
- If the account doesn't exist or the call fails, `PromiseResult::Failed` is hit and the registrar check silently degrades to "not registered" behavior via error path, so a relayer that mis-targets an eth-implicit account instead of the correct named account is **not detected and not banned** — the faulty-relayer safety net designed to catch this class of misdirection is defeated.
- Since `is_valid_target` in `validate_tx_relayer_data` already allows `TargetKind::EthImplicit` as a valid target class [8](#0-7) , the only remaining protection against a relayer sending funds/actions to the wrong (implicit vs. named) receiver is precisely this registrar cross-check, which is broken by the wrong hardcoded account on non-mainnet builds.

### Impact Explanation
On any NEAR network other than mainnet (testnet, localnet, and any private/permissioned deployment built from this crate), the Wallet Contract's registrar-based relayer-honesty check is non-functional because it references an account that does not correspond to the real registrar on that network. A relayer (which need not be the account owner) can therefore route actions/transfers intended for a registered named account (e.g., a NEP-141 token contract) to the raw eth-implicit account instead, without triggering the ban-relayer safety mechanism, resulting in misdirected value/action execution that the protocol's own anti-lazy-relayer design was meant to prevent.

### Likelihood Explanation
Any unprivileged relayer submitting a `rlp_execute` transaction against a Wallet Contract instance deployed from the `testnet`/`localnet` build artifacts can trigger this path; no special privileges are required beyond acting as a relayer of a signed Ethereum-style transaction, which is the intended (untrusted) role in this design. The bug is deterministic and always present in those build artifacts since the source file is never parameterized per network the way `CHAIN_ID` is.

### Recommendation
Parameterize `ADDRESS_REGISTRAR_ACCOUNT_ID` the same way `CHAIN_ID` is handled in `runtime/near-wallet-contract/build.rs` — write the correct per-network registrar account id into the source file before each of the three `build_contract` invocations (mainnet/testnet/localnet), and restore the original afterward, so each compiled artifact references the registrar that is actually deployed on its target network.

### Proof of Concept
1. Run `cargo build` for `near-wallet-contract`; observe `build.rs` rewrites `wallet-contract/src/CHAIN_ID` for each of the three targets but never touches `wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID` [4](#0-3) .
2. Inspect the resulting `wallet_contract_testnet.wasm` / `wallet_contract_localnet.wasm`: the embedded registrar account is still `address-map.near` [1](#0-0) .
3. Deploy this testnet/localnet artifact for a user's eth-implicit account; register a named account (e.g. a token contract) with the *actual* testnet/localnet registrar contract.
4. Have a relayer submit an `rlp_execute` transaction whose `target` is set to the eth-implicit form of that registered address (instead of the correct named account). Since `address-map.near` does not exist/is unrelated on the network, `address_check_callback` receives `PromiseResult::Failed`, and the faulty relayer is never banned; the action executes against the eth-implicit target instead of the named account.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/ADDRESS_REGISTRAR_ACCOUNT_ID (L1-1)
```text
address-map.near
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-174)
```rust
    #[private]
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L417-425)
```rust
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
```

**File:** runtime/near-wallet-contract/build.rs (L18-43)
```rust
fn main() -> anyhow::Result<()> {
    let contract_dir = "./implementation";

    build_contract(
        contract_dir,
        "eth_wallet_contract",
        "wallet_contract_mainnet",
        MAINNET_CHAIN_ID,
    )
    .context("Mainnet build failed")?;

    build_contract(
        contract_dir,
        "eth_wallet_contract",
        "wallet_contract_testnet",
        TESTNET_CHAIN_ID,
    )
    .context("Testnet build failed")?;

    build_contract(
        contract_dir,
        "eth_wallet_contract",
        "wallet_contract_localnet",
        LOCALNET_CHAIN_ID,
    )
    .context("Localnet build failed")?;
```

**File:** runtime/near-wallet-contract/build.rs (L63-73)
```rust
    let absolute_dir = Path::new(dir).canonicalize()?;

    let chain_id_path = absolute_dir.join("wallet-contract/src/CHAIN_ID");
    let chain_id_content = std::fs::read(&chain_id_path).context("Failed to read CHAIN_ID file")?;

    // Update the chain id before building
    std::fs::write(&chain_id_path, chain_id.to_string().into_bytes())?;
    docker_build(absolute_dir.to_str().expect("path should be valid UTF-8"))?;

    // Restore chain id file to original value after building
    std::fs::write(&chain_id_path, chain_id_content)?;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L337-346)
```rust
    let is_valid_target = match target_kind {
        TargetKind::CurrentAccount if to == context.current_address => {
            target == &context.current_account_id
        }
        TargetKind::EthImplicit(address) if to == address => {
            target.as_str()
                == format!("0x{}{}", hex::encode(address), context.current_account_suffix())
        }
        _ => to == account_id_to_address(target),
    };
```
