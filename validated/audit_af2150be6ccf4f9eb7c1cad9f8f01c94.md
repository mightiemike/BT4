### Title
Wallet Contract's Ethereum-style Chain-ID Check Uses a Build-Time Constant Instead of the Live NEAR Chain ID, Enabling Cross-Network Replay of ETH-Implicit Account Transactions - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`)

### Summary
The NEAR Wallet Contract (the global contract that lets ETH-implicit accounts sign Ethereum-style RLP transactions to control a NEAR account) checks the embedded EIP-155 `chain_id` field of the user's signed Ethereum transaction against a `CHAIN_ID` value that is baked into the WASM at *build time*, rather than being derived from the actual, live NEAR network the contract is running on. This is the same bug class as the Brink `EIP712SignerRecovery.sol` finding: the "unique chain identifier" is supplied by the deployer/build pipeline instead of being read on-chain, so the same signed message can be valid across more than one deployment.

### Finding Description
`CHAIN_ID` is read from a file at compile time and hard-coded into the contract binary: [1](#0-0) 

That constant is what gates every relayed Ethereum-style transaction: [2](#0-1) 

The value is chosen by a build script per target network (mainnet=397, testnet=398, localnet, etc.), analogous to how Brink's constructor argument fixed `_chainId` per deployment: [3](#0-2) [4](#0-3) 

Critically, nearcore already exposes a runtime host function that a contract could use to read the *actual* live chain identifier dynamically (the equivalent of Solidity's `block.chainid`), but the wallet contract does not use it for this check: [5](#0-4) 

Instead, which pre-compiled WASM (and therefore which baked-in `CHAIN_ID`) is used for a given network is resolved purely by a string match on the genesis `chain_id`, and the mapping explicitly reuses the *same* mainnet WASM/constant for more than one logical network: [6](#0-5) 

Because `MAINNET` and `MOCKNET` are mapped to the identical global-contract hash (and therefore the identical embedded `CHAIN_ID = 397`), any network that self-identifies with a chain_id string resolving to that same WASM instance will accept exactly the same EIP-155-tagged Ethereum transaction signature that is valid on canonical NEAR mainnet. There is no cryptographic binding between the signed transaction's `chain_id` field and the specific network instance actually executing it — the binding only exists insofar as the deployer/build tooling chose the "right" constant for the "right" network name.

### Impact Explanation
An end user signs an Ethereum-style RLP transaction (e.g. a `Transfer` or ERC-20-emulated action) for their ETH-implicit NEAR account, embedding `chain_id = 397`. This signature is checked only against the contract's compiled-in constant, not against any protocol-level, on-chain-verifiable identifier of the network actually processing the call. Any other network deployment that runs the identical wallet-contract WASM (as nearcore's own mapping shows happens for at least `MAINNET`/`MOCKNET`, and could happen for any future or shadow/forked NEAR-protocol chain that reuses the same global contract hash or the same build artifact) will accept and execute that same signed transaction, moving funds or performing actions on that account without a fresh authorization from the user. This is unauthorized value movement/execution triggered purely by a submitted (replayed) transaction, matching the accepted impact category.

### Likelihood Explanation
Likelihood is limited by how NEAR actually manages global contract deployment: `code_hash_matches_wallet_contract` and `eth_wallet_global_contract_hash` are used by the protocol/runtime to decide which contract code applies to an ETH-implicit account per `chain_id`, so in practice the same wallet-contract hash is deliberately reused for `MAINNET` and `MOCKNET` today. Any additional NEAR-protocol-compatible network (fork, shadow chain, or renamed/rebranded testing network) that adopts a `chain_id` string mapping to an existing entry in `wallet_contract_magic_bytes`/`eth_wallet_global_contract_hash` inherits the same baked-in constant and the same replay exposure, with no additional on-chain safeguard preventing it. This requires an operational/deployment decision (reusing the mainnet artifact hash on another network) rather than a purely remote unauthorized action, which is why the original Brink report and Spearbit's own resolution treated the underlying "deployer specifies vs. reads on-chain" pattern as an accepted, low-likelihood risk rather than a high-severity flaw.

### Recommendation
Have the wallet contract read the live network identifier dynamically instead of relying solely on a build-time constant selected by the build/deployment tooling — e.g., validate the Ethereum transaction's `chain_id` against a value derived from the genesis `chain_id` exposed to the runtime (the same host mechanism already surfaced in `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`), or ensure the global contract hash (and thus the embedded constant) is never intentionally shared across two distinct logical networks in `eth_wallet_global_contract_hash`/`wallet_contract_magic_bytes`. At minimum, document and enforce (e.g. via a protocol-level assertion) that any network sharing a wallet-contract global hash must be provably the same trust domain as the network the constant was built for.

### Proof of Concept
1. Network A (canonical NEAR mainnet, `chain_id = "mainnet"`) runs the wallet-contract global contract whose embedded `CHAIN_ID = 397`, per `wallet_contract_magic_bytes`/`eth_wallet_global_contract_hash` in `runtime/near-wallet-contract/src/lib.rs:74-105`.
2. Network B is any NEAR-protocol-compatible deployment (e.g. `MOCKNET`, or a future fork/shadow chain) whose genesis `chain_id` resolves to the same global contract hash entry, per the same mapping (`chains::MAINNET | chains::MOCKNET => ...` in `eth_wallet_global_contract_hash`).
3. A user signs an Ethereum-style RLP transaction (`chain_id = 397`) authorizing a transfer from their ETH-implicit account, intending it to execute only on canonical mainnet.
4. Because the wallet contract's on-chain check in `validate_tx_relayer_data` (`internal.rs:328`) only compares against the compiled-in `CHAIN_ID` constant — identical on both networks — a relayer (or the user themselves) can submit the exact same signed RLP bytes via `rlp_execute` on Network B, and it passes validation and executes there as well, since nothing in the check is bound to which live network is actually processing the request.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L16-20)
```rust
/// The chain ID is pulled from a file to allow this contract to be easily
/// compiled with the appropriate value for the network it will be deployed on.
/// The chain ID for Near mainnet is [397](https://chainlist.org/chain/397)
/// while the value for testnet is [398](https://chainlist.org/chain/398).
pub const CHAIN_ID: u64 = std::include!("CHAIN_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L324-330)
```rust
    if tx.address.raw() != context.current_address {
        return Err(Error::Relayer(RelayerError::InvalidSender));
    }

    if tx.chain_id != Some(CHAIN_ID) {
        return Err(Error::Relayer(RelayerError::InvalidChainId));
    }
```

**File:** runtime/near-wallet-contract/build.rs (L51-83)
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

    let build_artifact_path =
        format!("target/wasm32-unknown-unknown/release/{}.wasm", contract_name);
    let src = absolute_dir.join(build_artifact_path);

    std::fs::copy(&src, &wasm_target_path)
        .with_context(|| format!("Failed to copy `{}` to `{}`", src.display(), wasm_target_path))?;
    Ok(())
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L571-589)
```rust
/// Saves the chain ID of the current chain into the register.
///
/// # Errors
///
/// If the registers exceed the memory limit returns `MemoryAccessViolation`.
///
/// # Cost
///
/// `base + write_register_base + write_register_byte * num_bytes`
pub fn chain_id(ctx: &mut Ctx, _memory: &mut [u8], register_id: u64) -> Result<()> {
    ctx.result_state.gas_counter.pay_base(base)?;
    let chain_id = ctx.ext.chain_id();
    ctx.registers.set(
        &mut ctx.result_state.gas_counter,
        &ctx.config.limit_config,
        register_id,
        chain_id.as_bytes(),
    )
}
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
