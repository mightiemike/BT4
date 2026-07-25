### Title
Static compile-time `CHAIN_ID` in Wallet Contract enables cross-fork Ethereum transaction replay against ETH-implicit accounts — (`runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`)

---

### Summary

The Wallet Contract, which governs all ETH-implicit accounts on NEAR, validates incoming Ethereum transactions against a compile-time constant `CHAIN_ID` (397 on mainnet, 398 on testnet). Because this value is baked into the WASM binary at build time and never re-read from runtime context, any hard fork that produces two live chains sharing the same EVM chain ID leaves every ETH-implicit account's signed Ethereum transactions replayable across both chains. An unprivileged attacker who collects a victim's signed `rlp_execute` payload from one chain can submit it verbatim on the sibling chain, executing the encoded NEAR action (transfer, function call, key management) a second time and draining the victim's balance.

---

### Finding Description

`validate_tx_relayer_data` in `internal.rs` performs the chain-ID guard:

```rust
if tx.chain_id != Some(CHAIN_ID) {
    return Err(Error::Relayer(RelayerError::InvalidChainId));
}
```

where `CHAIN_ID` is declared as:

```rust
pub const CHAIN_ID: u64 = std::include!("CHAIN_ID");
```

and the included file contains the literal integer `397` (mainnet). [1](#0-0) [2](#0-1) [3](#0-2) 

The wallet contract's per-account nonce (`self.nonce`) is stored in the account's trie state. [4](#0-3) 

At the moment of a hard fork the entire trie state — including every ETH-implicit account's `nonce` field — is duplicated identically on both chains. Because the wallet contract binary is also identical on both chains (same code hash, same embedded `CHAIN_ID = 397`), the two replay-protection inputs that `validate_tx_relayer_data` checks — `tx.chain_id` and `tx.nonce` — are simultaneously valid on both chains for any transaction signed before the fork.

The wallet contract is automatically assigned to every ETH-implicit account at creation time via `eth_wallet_global_contract_hash`, which is keyed on the NEAR string chain ID (`"mainnet"`, `"testnet"`) but resolves to a fixed code hash that embeds the static EVM integer. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Any ETH-implicit account holder who signed an Ethereum transaction before a fork (or whose relayer cached such a transaction) is exposed. The replayed `rlp_execute` call can encode:

- A `Transfer` action — directly moves NEAR tokens out of the victim's account.
- A `FunctionCall` action — calls an arbitrary contract on behalf of the victim.
- An `AddKey` / `DeleteKey` action — installs or removes access keys, enabling further unauthorized transactions.

All of these map to the "stealing or loss of funds," "unauthorized transaction," and "balance manipulation" impact categories in the allowed scope.

---

### Likelihood Explanation

A NEAR hard fork is a low-probability but non-zero event (protocol upgrades, governance disputes, emergency patches). The Ethereum ecosystem has demonstrated that hard forks do occur and that static chain-ID bugs are exploited immediately afterward (cf. the ETHPoW replay incidents). Because the wallet contract is protocol-level infrastructure that cannot be upgraded by individual users, every ETH-implicit account on the forked chain is affected simultaneously with no opt-out path. The attacker's action — submitting a previously broadcast `rlp_execute` call — requires no special privilege.

---

### Recommendation

Replace the compile-time constant with a value derived from the runtime context. The NEAR VM already exposes the chain ID to contracts via the `chain_id` host function. The wallet contract should read the EVM chain ID from a value that is either:

1. **Stored in contract state at initialization** and compared against `env::chain_id()` on every call (analogous to the EIP-712 pattern of caching and re-checking), or
2. **Derived deterministically from `env::chain_id()`** (the NEAR string chain ID) at call time, so that a forked chain with a different NEAR chain ID automatically produces a different EVM chain ID.

A minimal diff:

```diff
-pub const CHAIN_ID: u64 = std::include!("CHAIN_ID");
+fn expected_chain_id() -> u64 {
+    // Derive from the runtime NEAR chain ID so forks with a new
+    // chain_id string automatically get a distinct EVM chain ID.
+    match near_sdk::env::chain_id().as_str() {
+        "mainnet" => 397,
+        "testnet" => 398,
+        _ => panic!("unknown chain"),
+    }
+}
```

and in `validate_tx_relayer_data`:

```diff
-if tx.chain_id != Some(CHAIN_ID) {
+if tx.chain_id != Some(expected_chain_id()) {
```

This ensures that a forked chain that adopts a new NEAR chain ID string will reject transactions signed for the original chain's EVM chain ID.

---

### Proof of Concept

**Setup**: NEAR mainnet hard forks at block `H`. Both chains (`mainnet-A` and `mainnet-B`) run the same nearcore binary, so both carry the wallet contract with `CHAIN_ID = 397`. Alice's ETH-implicit account has `nonce = 5` on both chains (state was identical at block `H`).

**Step 1 — Victim signs on chain A**:
Alice signs an Ethereum EIP-2930 transaction with `chain_id = 397`, `nonce = 5`, encoding a `Transfer` of 10 NEAR to Bob. Her relayer submits it to chain A via `rlp_execute`. The wallet contract accepts it (`chain_id` matches, `nonce` matches), increments `self.nonce` to 6, and executes the transfer. Alice's balance on chain A decreases by 10 NEAR.

**Step 2 — Attacker replays on chain B**:
The attacker (who observed the signed Ethereum transaction bytes on chain A) submits the identical `rlp_execute(target, tx_bytes_b64)` call to Alice's ETH-implicit account on chain B. The wallet contract on chain B checks:
- `tx.chain_id == Some(397)` → **passes** (same static constant)
- `tx.nonce == self.nonce` (which is still 5 on chain B) → **passes**
- Signature verification against Alice's Secp256k1 key → **passes** (same key, same signed bytes)

The contract executes the transfer, sending 10 NEAR from Alice's chain-B account to Bob's chain-B account. Alice loses 10 NEAR on chain B without authorizing any action on that chain. [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L20-20)
```rust
pub const CHAIN_ID: u64 = std::include!("CHAIN_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L318-368)
```rust
fn validate_tx_relayer_data<'a>(
    tx: &NormalizedEthTransaction,
    target: &'a AccountId,
    context: &ExecutionContext,
    expected_nonce: u64,
) -> Result<TargetKind<'a>, Error> {
    if tx.address.raw() != context.current_address {
        return Err(Error::Relayer(RelayerError::InvalidSender));
    }

    if tx.chain_id != Some(CHAIN_ID) {
        return Err(Error::Relayer(RelayerError::InvalidChainId));
    }

    let to = tx.to.ok_or(Error::User(UserError::EvmDeployDisallowed))?.raw();

    let target_kind = parse_target(target, context.current_address);

    // valid targets satisfy `to == target` or `to == hash(target)`
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

    if !is_valid_target {
        return Err(Error::Relayer(RelayerError::InvalidTarget));
    }

    let nonce = if tx.nonce <= U64_MAX {
        tx.nonce.low_u64()
    } else {
        return Err(Error::Relayer(RelayerError::InvalidNonce));
    };
    if nonce != expected_nonce {
        return Err(Error::Relayer(RelayerError::InvalidNonce));
    }

    // Relayers must attach at least as much gas as the user requested.
    let gas_limit = if tx.gas_limit < U64_MAX { tx.gas_limit.as_u64() } else { u64::MAX };
    if env::prepaid_gas().as_gas() < gas_limit.saturating_mul(GAS_MULTIPLIER) {
        return Err(Error::Relayer(RelayerError::InsufficientGas));
    }

    Ok(target_kind)
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/CHAIN_ID (L1-8)
```text
{
    // The contents of this file are set by the build file in `near-wallet-contract`.
    // The purpose is to factor out the hard-coded EVM chain ID so that it can be set for
    // each network (mainnet, testnet, etc). The value included in this file is the
    // [one for mainnet](https://chainlist.org/chain/397). The one for testnet is
    // [398](https://chainlist.org/chain/398).
    397
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L46-55)
```rust
pub struct WalletContract {
    pub nonce: u64,
    /// Tracks whether a transaction is currently being executed
    /// (i.e. has receipts that have not yet resolved).
    /// Invariant: `has_in_flight_tx` must be `true` when a mutable method
    /// of this contract returns a promise and `false` otherwise (except
    /// for the check if a transaction is already in flight at the beginning
    /// of `rlp_execute`).
    pub has_in_flight_tx: bool,
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L89-128)
```rust
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
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

**File:** runtime/near-wallet-contract/src/lib.rs (L89-105)
```rust
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

**File:** runtime/runtime/src/actions.rs (L232-245)
```rust
        AccountType::EthImplicitAccount => {
            let chain_id = epoch_info_provider.chain_id();

            // Use a deployed global contract for ETH implicit accounts.
            let global_contract_hash = eth_wallet_global_contract_hash(&chain_id);
            let storage_usage = fee_config.storage_usage_config.num_bytes_account
                + global_contract_hash.as_bytes().len() as u64;

            *account = Some(Account::new(
                deposit,
                Balance::ZERO,
                AccountContract::Global(global_contract_hash),
                storage_usage,
            ));
```
