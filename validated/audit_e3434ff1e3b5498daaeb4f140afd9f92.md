### Title
Ethereum block assembler hardcodes `base_fee_per_gas = 0` in EIP‑4844→Osaka excess‑blob‑gas formula on the first post‑Cancun block, diverging from the consensus validator's parent‑fee input - (File: crates/ethereum/evm/src/build.rs)

### Summary
`EthBlockAssembler::assemble_block` and `validate_against_parent_4844` compute `excess_blob_gas` using two different formulas for the special case of "the first block after Cancun activates." The assembler hardcodes all three inputs to `next_block_excess_blob_gas_osaka` as zero — including the parent base fee — while the consensus validator that will later check the very same header derives the base-fee input from the parent header's real `base_fee_per_gas`. This is analogous to the GammaSwap bug: a formula that should be computed consistently from real state on both sides of an equality instead has one side use a hardcoded/wrong operand instead of the actual value.

### Finding Description
In the block builder: [1](#0-0) 

```rust
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

When the parent block is not yet Cancun-active (i.e., the block being assembled is the first post-Cancun block), the assembler calls `next_block_excess_blob_gas_osaka(0, 0, 0)` — passing zero for `parent_excess_blob_gas`, `parent_blob_gas_used`, **and** `parent_base_fee_per_gas`.

Compare this to the consensus-side validator used to check headers built by this same assembler: [2](#0-1) 

```rust
pub fn validate_against_parent_4844<H: BlockHeader>(
    header: &H,
    parent: &H,
    blob_params: BlobParams,
) -> Result<(), ConsensusError> {
    // ... From EIP-4844: parent.blob_gas_used and parent.excess_blob_gas are 0 for first post-fork block
    let parent_blob_gas_used = parent.blob_gas_used().unwrap_or(0);
    let parent_excess_blob_gas = parent.excess_blob_gas().unwrap_or(0);
    ...
    let parent_base_fee_per_gas = parent.base_fee_per_gas().unwrap_or(0);
    let expected_excess_blob_gas = blob_params.next_block_excess_blob_gas_osaka(
        parent_excess_blob_gas,
        parent_blob_gas_used,
        parent_base_fee_per_gas,
    );
    ...
}
```

The EIP‑4844 spec text only says that `parent.blob_gas_used` and `parent.excess_blob_gas` are evaluated as zero for the first post-fork block — it does not say `parent.base_fee_per_gas` should be zeroed. `validate_against_parent_4844` correctly reads the parent's actual (non-zero, since London activates long before Cancun on any real network) `base_fee_per_gas` and feeds it into `next_block_excess_blob_gas_osaka`. The block assembler, however, unconditionally hardcodes this third argument to `0` in the "parent not yet Cancun" branch — losing the real base fee input entirely, just as GammaSwap's `getInvariantFactor()` silently dropped one of the two economically meaningful inputs (`s.decimals[1]`) from its formula.

The `_osaka` suffix on `next_block_excess_blob_gas_osaka` indicates it implements the post‑Osaka formula (EIP‑7918, "blob base fee bounded by execution cost"), which — unlike the original EIP‑4844 `calc_excess_blob_gas` — explicitly incorporates the execution base fee into the excess-blob-gas computation as a reserve-price floor. Because the `_osaka` variant is base-fee-sensitive, feeding it a hardcoded `0` instead of the parent's real base fee can silently change the computed value, whereas the pre-Osaka formula (a pure `max(excess+used-target, 0)`) would not be affected by this discrepancy.

### Impact Explanation
If a chain configuration causes a node to build the first post-Cancun (or first post-Cancun-with-Osaka-blob-params) block while the EIP-7918 base-fee-dependent term is active and the parent's real base fee differs from zero (true for essentially any live/EL-London chain), the self-built header's `excess_blob_gas` would be computed with the wrong (zeroed) base-fee input by `EthBlockAssembler`. If this diverges from what `validate_against_parent_4844` — reth's own consensus validator, and by extension what other conformant clients — expects using the correct parent base fee, the locally produced block would fail its own post-execution/header consensus check, or be built with a header field other clients reject. This falls into the "reth-built blocks rejected by other clients" / "reth-built block fails its own validation" impact category (High), since it can stall block production or cause chain splits at a hardfork boundary that is inherently non-malicious (triggered purely by fork-activation timing, not an attacker).

### Likelihood Explanation
This code path only triggers once per chain — at the exact block where Cancun (or any hardfork enabling `BlobParams` with the Osaka reserve-price formula) transitions from inactive to active on the parent. It is deterministic and unconditional (not requiring any attacker), but it is a narrow, one-time transition window per chain/spec configuration (relevant primarily to devnets/testnets configuring these forks, since mainnet's Cancun→pre-Cancun transition already occurred under the older non-Osaka formula). Likelihood is Medium given exposure is limited to fork-transition blocks and depends on whether EIP-7918's base-fee term is exercised in that specific formula variant, which could not be fully confirmed from in-scope source (the formula body lives in the external `alloy_eips` crate, outside repo scope).

### Recommendation
In `crates/ethereum/evm/src/build.rs`, when the parent is not yet Cancun-active, do not hardcode `parent_base_fee_per_gas` to `0`. Instead mirror `validate_against_parent_4844`'s approach: keep `parent_excess_blob_gas` and `parent_blob_gas_used` at `0` per the EIP-4844 spec, but pass the parent's actual `base_fee_per_gas` (`parent.base_fee_per_gas.unwrap_or(0)`) to `next_block_excess_blob_gas_osaka`, and use `self.chain_spec.blob_params_at_timestamp(timestamp)` for the `BlobParams` selection instead of hardcoding `BlobParams::cancun()`, ensuring the assembler and the consensus validator always compute this value identically.

### Proof of Concept
Not independently reproducible from the indexed subset of this repo, because the exact numerical behavior of `next_block_excess_blob_gas_osaka` (specifically whether/how the third argument affects the result when the first two are zero) is implemented in the external `alloy_eips` crate, which is out of scope and not indexed here. The divergence itself — `build.rs` passing a hardcoded `0` for the parent base fee vs. `validation.rs` passing the parent's real `base_fee_per_gas` into the same function — is directly verifiable in the cited source lines. A concrete before/after PoC (constructing a chain spec that activates Cancun after a nonzero-base-fee parent, then diffing `EthBlockAssembler::assemble_block`'s `excess_blob_gas` output against `validate_against_parent_4844`'s `expected_excess_blob_gas`) would need to be executed with access to the `alloy_eips` implementation to confirm whether the two values actually differ in practice.

### Citations

**File:** crates/ethereum/evm/src/build.rs (L97-112)
```rust
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

**File:** crates/consensus/common/src/validation.rs (L417-448)
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
```
