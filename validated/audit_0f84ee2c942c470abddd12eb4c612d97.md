### Title
Hardcoded EVM `CHAIN_ID` in Wallet Contract Enables Cross-Fork Replay of ETH Implicit Account Transactions - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`)

---

### Summary

The ETH implicit account wallet contract bakes the EVM chain ID (397 for mainnet, 398 for testnet) into the WASM binary at compile time via a `std::include!` macro. If NEAR were to hard fork, both resulting chains would run the same wallet contract WASM with the same hardcoded `CHAIN_ID`. Because the only cross-chain replay guard in `validate_tx_relayer_data` is this static constant, a signed Ethereum-style transaction from one chain can be replayed on the other chain by any unprivileged caller of `rlp_execute`.

---

### Finding Description

The wallet contract's `CHAIN_ID` is a compile-time constant: [1](#0-0) 

The value is written into the WASM binary during the `build.rs` build step — 397 for mainnet, 398 for testnet, 399 for localnet: [2](#0-1) 

The `validate_tx_relayer_data` function uses this constant as the **sole cross-chain discriminator**: [3](#0-2) 

The wallet contract's per-account nonce is stored in contract state: [4](#0-3) 

If NEAR mainnet forks, both chains initially run the **same wallet contract WASM** (embedded in nearcore as a static binary) with `CHAIN_ID=397`. The wallet contract's nonce is also identical on both chains at the fork point because both chains share the same state history up to the fork block.

Therefore, a signed Ethereum transaction with `chain_id=397` and `nonce=N` that is valid on chain A is equally valid on chain B:
1. Both chains accept `chain_id=397` (same hardcoded constant)
2. Both chains have `nonce=N` at the fork point

The `rlp_execute` entry point has no access control — it is callable by any NEAR account: [5](#0-4) 

An attacker who observes the `tx_bytes_b64` argument in a NEAR transaction calling `rlp_execute` on chain A can submit a new NEAR transaction to chain B calling `rlp_execute` on the same wallet contract with the same Ethereum transaction bytes. The chain ID check passes (both chains have `CHAIN_ID=397`) and the nonce check passes (chain B still has `nonce=N`).

---

### Impact Explanation

- **Unauthorized transaction execution**: The attacker replays a transaction the user signed for chain A on chain B without the user's consent.
- **Loss of funds**: If the replayed transaction is a `Transfer` action, the attacker drains the user's ETH implicit account balance on chain B.
- **Broken authorization invariant**: The wallet contract's chain ID check is the designated cross-chain replay guard, but it fails to distinguish between two chains that share the same hardcoded `CHAIN_ID` value baked into the WASM binary.

The corrupted value is the wallet contract's nonce on chain B: it is consumed by the replayed transaction, and the user's funds are transferred without authorization.

---

### Likelihood Explanation

- Requires a NEAR hard fork to be exploitable — the same external precondition as the GolomTrader finding (confirmed medium risk).
- Hard forks can and do happen; NEAR has undergone protocol upgrades and network splits before.
- The wallet contract is embedded in nearcore as a static WASM binary and can only be updated via a protocol upgrade. During the transition period after a fork, both chains run the same wallet contract WASM with the same chain ID.
- The attack is trivially executable by any unprivileged user who can read NEAR transactions on the blockchain (all transaction arguments are public).
- No privileged role, validator access, or key compromise is required.

---

### Recommendation

The wallet contract should dynamically incorporate the NEAR chain ID (available via the `chain_id` host function, which returns the NEAR chain ID string such as `"mainnet"` or `"testnet"`) into the domain separator used for Ethereum transaction validation. This would ensure the domain separator is chain-specific even after a fork, because the two chains would have different NEAR chain IDs.

Concretely, the `validate_tx_relayer_data` function should replace the static `CHAIN_ID` check with a check that combines the EVM chain ID with the runtime-provided NEAR chain ID, so that a transaction signed for one fork cannot be accepted on the other. [6](#0-5) 

---

### Proof of Concept

1. NEAR mainnet forks into two chains: A (`mainnet`) and B (`mainnet-fork`).
2. Both chains run the same wallet contract WASM with `CHAIN_ID=397` (hardcoded at compile time).
3. Both chains have the same wallet contract nonce `N` at the fork point (identical state).
4. A user signs an Ethereum-style transaction for chain A:
   ```
   Transaction2930 {
       chain_id: 397,   // CHAIN_ID for mainnet — same on both chains
       nonce: N,        // current nonce on both chains at fork
       to: Some(receiver_address),
       value: Wei::new_u128(transfer_amount),
       ...
   }
   ```
5. The user submits the transaction to chain A via `rlp_execute`. Nonce on chain A increments to `N+1`.
6. An attacker reads the `tx_bytes_b64` argument from the NEAR transaction on chain A (public blockchain data).
7. The attacker submits a new NEAR transaction to chain B calling `rlp_execute` on the user's wallet contract with the same `tx_bytes_b64`.
8. `validate_tx_relayer_data` on chain B:
   - `tx.chain_id == Some(397) == Some(CHAIN_ID)` → **passes** (same hardcoded constant)
   - `nonce == N == expected_nonce` → **passes** (chain B still has `nonce=N`)
9. The Ethereum transaction executes on chain B, transferring funds from the user's wallet without authorization. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L16-20)
```rust
/// The chain ID is pulled from a file to allow this contract to be easily
/// compiled with the appropriate value for the network it will be deployed on.
/// The chain ID for Near mainnet is [397](https://chainlist.org/chain/397)
/// while the value for testnet is [398](https://chainlist.org/chain/398).
pub const CHAIN_ID: u64 = std::include!("CHAIN_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L318-330)
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
```

**File:** runtime/near-wallet-contract/build.rs (L9-16)
```rust
/// See https://chainlist.org/chain/397
const MAINNET_CHAIN_ID: u64 = 397;

/// See https://chainlist.org/chain/398
const TESTNET_CHAIN_ID: u64 = 398;

/// Not officially registered on chainlist.org because this is for local testing only.
const LOCALNET_CHAIN_ID: u64 = 399;
```

**File:** runtime/near-wallet-contract/build.rs (L51-73)
```rust
fn build_contract(
    dir: &str,
    contract_name: &str,
    output: &str,
    chain_id: u64,
) -> anyhow::Result<()> {
    let wasm_target_path = format!("./res/{}.wasm", output);
    if Path::new(&wasm_target_path).exists() {
        // Skip building if an artifact is already present
        return Ok(());
    }

    let absolute_dir = Path::new(dir).canonicalize()?;

    let chain_id_path = absolute_dir.join("wallet-contract/src/CHAIN_ID");
    let chain_id_content = std::fs::read(&chain_id_path).context("Failed to read CHAIN_ID file")?;

    // Update the chain id before building
    std::fs::write(&chain_id_path, chain_id.to_string().into_bytes())?;
    docker_build(absolute_dir.to_str().expect("path should be valid UTF-8"))?;

    // Restore chain id file to original value after building
    std::fs::write(&chain_id_path, chain_id_content)?;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L46-54)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/CHAIN_ID (L1-7)
```text
{
    // The contents of this file are set by the build file in `near-wallet-contract`.
    // The purpose is to factor out the hard-coded EVM chain ID so that it can be set for
    // each network (mainnet, testnet, etc). The value included in this file is the
    // [one for mainnet](https://chainlist.org/chain/397). The one for testnet is
    // [398](https://chainlist.org/chain/398).
    397
```
