### Title
Ethereum block assembler hardcodes default Cancun blob params for the first post-Cancun block instead of chain-spec-configured params - ([File: crates/ethereum/evm/src/build.rs])

### Summary
`EthBlockAssembler::assemble_block` computes `excess_blob_gas` for the first post-Cancun block using a hardcoded `alloy_eips::eip7840::BlobParams::cancun()` instead of the chain-spec-configured blob params returned by `EthChainSpec::blob_params_at_timestamp`, which the consensus-side validator (`validate_against_parent_4844`) correctly uses. On any chain whose genesis config overrides the Cancun blob schedule (`blobSchedule` per EIP-7840/7892, exposed via `EthChainSpec::blob_params_at_timestamp`), reth-built blocks compute a different `excess_blob_gas` than reth's own validator and any spec-conformant client would expect.

### Finding Description
`EthBlockAssembler::assemble_block` in [1](#0-0)  branches on whether the parent was already Cancun-active:

```
if self.chain_spec.is_cancun_active_at_timestamp(timestamp) {
    block_blob_gas_used = Some(*blob_gas_used);
    excess_blob_gas = if self.chain_spec.is_cancun_active_at_timestamp(parent.timestamp) {
        parent.maybe_next_block_excess_blob_gas(
            self.chain_spec.blob_params_at_timestamp(timestamp),
        )
    } else {
        // for the first post-fork block, both parent.blob_gas_used and
        // parent.excess_blob_gas are evaluated as 0
        Some(
            alloy_eips::eip7840::BlobParams::cancun()
                .next_block_excess_blob_gas_osaka(0, 0, 0),
        )
    };
}
```

For every block after the first post-Cancun block, the correct chain-spec-resolved params (`self.chain_spec.blob_params_at_timestamp(timestamp)`) are used. But for the very first post-Cancun block, the code uses the library-default `BlobParams::cancun()` (target=3, max=6 blobs by upstream default) instead of calling `self.chain_spec.blob_params_at_timestamp(timestamp)`.

This is inconsistent with the validator path in [2](#0-1) , which always resolves `blob_params` via `self.chain_spec.blob_params_at_timestamp(header.timestamp())` before calling `validate_against_parent_4844`, and with `validate_against_parent_4844` itself in [3](#0-2) , which computes `expected_excess_blob_gas` from the passed-in (chain-spec-resolved) `blob_params`, not a hardcoded default.

`EthChainSpec::blob_params_at_timestamp` is chain-spec-configurable via the genesis `blobSchedule` field per EIP-7840/EIP-7892 (`BlobScheduleBlobParams`), as referenced in [4](#0-3)  and declared in [5](#0-4) . If a chain configures a non-default Cancun blob schedule (e.g. a devnet or L2 chain that raises/lowers the Cancun target/max blob count from the library defaults), then for the first block after the Cancun transition:
- The block builder computes `excess_blob_gas` using the hardcoded `BlobParams::cancun()` defaults.
- The consensus validator (and any other spec-conformant client reading the same genesis config) computes the expected `excess_blob_gas` using the actually configured blob params from `blob_params_at_timestamp`.

These two values diverge whenever the configured Cancun blob params differ from the upstream defaults.

### Impact Explanation
This breaks the equality between a reth-built block and what reth's own validation (and any other node using the correctly configured genesis blob schedule) expects. Concretely: reth's own `validate_against_parent_4844` would reject the block reth itself produced (`ConsensusError::ExcessBlobGasDiff`), and any external client enforcing the correctly configured blob schedule would likewise reject it, since `excess_blob_gas` is a required consensus header field per EIP-4844/EIP-7840. This matches "reth-built blocks rejected by other clients" / "a valid chain marked invalid" category — a High severity impact per the given rubric, since it stalls block production/propagation exactly at every Cancun-activation boundary on any chain with a customized blob schedule.

### Likelihood Explanation
This triggers deterministically — no attacker or malicious input is required — on any chain configuration (custom chain spec/devnet/L2) where the genesis `blobSchedule` for Cancun differs from the hardcoded library default `BlobParams::cancun()`. It fires exactly once, at the single block transitioning from pre-Cancun to Cancun, but is completely deterministic and unavoidable under that configuration, matching the original report's failure mode (a value computed correctly in most cases but incorrectly at a specific transition boundary).

### Recommendation
Replace the hardcoded `alloy_eips::eip7840::BlobParams::cancun()` in `crates/ethereum/evm/src/build.rs` with `self.chain_spec.blob_params_at_timestamp(timestamp)` (falling back to `BlobParams::cancun()` only if the chain spec returns `None`), so the first post-fork block computation always uses the same chain-spec-resolved params as the validator path (`validate_against_parent_4844` / `crates/ethereum/consensus/src/lib.rs`).

### Proof of Concept
1. Configure a chain spec/genesis with a custom Cancun `blobSchedule` whose `target`/`max` differ from the alloy default `BlobParams::cancun()` (e.g., target=6, max=9), and Cancun active at genesis+N seconds while parent block is pre-Cancun.
2. Have reth build the first post-Cancun block via `EthBlockAssembler::assemble_block` — observe that `excess_blob_gas` is computed with `BlobParams::cancun().next_block_excess_blob_gas_osaka(0, 0, 0)`, i.e., using the default target/max rather than the configured ones.
3. Feed that same block through `reth_ethereum_consensus`'s `validate_header_against_parent`, which resolves `blob_params` via `chain_spec.blob_params_at_timestamp(header.timestamp())` (the custom, configured params) and calls `validate_against_parent_4844`.
4. Whenever the custom blob target/max differ from the alloy defaults, `next_block_excess_blob_gas_osaka` produces a different value in each path, and `validate_against_parent_4844` returns `ConsensusError::ExcessBlobGasDiff`, i.e., reth rejects the block it just built.

### Citations

**File:** crates/ethereum/evm/src/build.rs (L94-112)
```rust
        let mut excess_blob_gas = None;
        let mut block_blob_gas_used = None;

        // only determine cancun fields when active
        if self.chain_spec.is_cancun_active_at_timestamp(timestamp) {
            block_blob_gas_used = Some(*blob_gas_used);
            excess_blob_gas = if self.chain_spec.is_cancun_active_at_timestamp(parent.timestamp) {
                parent.maybe_next_block_excess_blob_gas(
                    self.chain_spec.blob_params_at_timestamp(timestamp),
                )
            } else {
                // for the first post-fork block, both parent.blob_gas_used and
                // parent.excess_blob_gas are evaluated as 0
                Some(
                    alloy_eips::eip7840::BlobParams::cancun()
                        .next_block_excess_blob_gas_osaka(0, 0, 0),
                )
            };
        }
```

**File:** crates/ethereum/consensus/src/lib.rs (L286-291)
```rust
        )?;

        // ensure that the blob gas fields for this block
        if let Some(blob_params) = self.chain_spec.blob_params_at_timestamp(header.timestamp()) {
            validate_against_parent_4844(header.header(), parent.header(), blob_params)?;
        }
```

**File:** crates/consensus/common/src/validation.rs (L417-451)
```rust
pub fn validate_against_parent_4844<H: BlockHeader>(
    header: &H,
    parent: &H,
    blob_params: BlobParams,
) -> Result<(), ConsensusError> {
    // From [EIP-4844](https://eips.ethereum.org/EIPS/eip-4844#header-extension):
    //
    // > For the first post-fork block, both parent.blob_gas_used and parent.excess_blob_gas
    // > are evaluated as 0.
    //
    // This means in the first post-fork block, calc_excess_blob_gas will return 0.
    let parent_blob_gas_used = parent.blob_gas_used().unwrap_or(0);
    let parent_excess_blob_gas = parent.excess_blob_gas().unwrap_or(0);

    if header.blob_gas_used().is_none() {
        return Err(ConsensusError::BlobGasUsedMissing)
    }
    let excess_blob_gas = header.excess_blob_gas().ok_or(ConsensusError::ExcessBlobGasMissing)?;

    let parent_base_fee_per_gas = parent.base_fee_per_gas().unwrap_or(0);
    let expected_excess_blob_gas = blob_params.next_block_excess_blob_gas_osaka(
        parent_excess_blob_gas,
        parent_blob_gas_used,
        parent_base_fee_per_gas,
    );
    if expected_excess_blob_gas != excess_blob_gas {
        return Err(ConsensusError::ExcessBlobGasDiff {
            diff: GotExpected { got: excess_blob_gas, expected: expected_excess_blob_gas },
            parent_excess_blob_gas,
            parent_blob_gas_used,
        })
    }

    Ok(())
}
```

**File:** crates/chainspec/src/spec.rs (L29-32)
```rust
use alloy_eips::{
    eip1559::INITIAL_BASE_FEE, eip7685::EMPTY_REQUESTS_HASH, eip7840::BlobParams,
    eip7892::BlobScheduleBlobParams, eip7928::EMPTY_BLOCK_ACCESS_LIST_HASH,
};
```

**File:** crates/chainspec/src/api.rs (L26-30)
```rust
    /// Get the [`BaseFeeParams`] for the chain at the given timestamp.
    fn base_fee_params_at_timestamp(&self, timestamp: u64) -> BaseFeeParams;

    /// Get the [`BlobParams`] for the given timestamp
    fn blob_params_at_timestamp(&self, timestamp: u64) -> Option<BlobParams>;
```
