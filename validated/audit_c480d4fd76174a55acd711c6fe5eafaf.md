### Title
`skip_pow_verification` Permanently Retained in Contract State Enables Irrevocable PoW Bypass - (File: `contract/src/lib.rs`)

---

### Summary

The `skip_pow_verification` boolean is stored as a permanent field in `BtcLightClient` contract state with no setter method to change it post-initialization. If the contract is deployed with this flag set to `true` (documented as "for testing purposes only"), it permanently disables all PoW verification for every subsequent `submit_blocks` call. There is no privileged method to reset it. A trusted relayer can then submit headers with arbitrary, invalid PoW, corrupting the canonical chain and causing `verify_transaction_inclusion` to return false positives.

---

### Finding Description

`skip_pow_verification: bool` is declared as a permanent field of the `BtcLightClient` contract state struct: [1](#0-0) 

It is assigned exactly once, at initialization, from the caller-supplied `InitArgs`: [2](#0-1) 

The documentation comment on `init` explicitly marks this flag as testing-only: [3](#0-2) 

There is no setter method anywhere in the contract to change this flag after initialization. A grep across the entire codebase confirms the only write site is the constructor assignment at line 142.

When `skip_pow_verification` is `true`, the entire PoW check branch in `submit_block_header` is skipped: [4](#0-3) 

For the Dogecoin build, the AuxPoW parent-block hash check is also gated on this flag: [5](#0-4) 

The `migrate` function explicitly carries the flag forward from the V2 state layout, so it survives contract upgrades: [6](#0-5) 

The analog to the external report is direct: the mnemonic is loaded at program start and kept in memory all the time with no mechanism to clear it; here, `skip_pow_verification=true` is written at contract initialization and kept in state permanently with no mechanism to reset it. Both represent a security-critical value that should be transient but is instead retained indefinitely.

---

### Impact Explanation

If `skip_pow_verification=true` is present in production state, any trusted relayer can call `submit_blocks` with a batch of headers whose `block_hash_pow()` does not satisfy `target_from_bits(header.bits)`. The contract accepts them unconditionally, updating `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `headers_pool` with fabricated chain data. [7](#0-6) 

Downstream callers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` then operate against a corrupted canonical chain. A fabricated block can be given any `merkle_root`, so the attacker can construct a Merkle proof for a transaction that never existed on the real Bitcoin network and have the contract return `true`. [8](#0-7) 

Any recipient contract that gates fund releases or cross-chain actions on a `true` result from this contract is directly exploitable.

---

### Likelihood Explanation

The likelihood is moderate. The flag is documented as testing-only, but there is no on-chain enforcement. A deployment script that was used for staging and then promoted to production, or a developer who forgot to flip the flag, leaves the contract permanently vulnerable. Because there is no setter, the only remediation path is a full contract redeployment via the `Upgradable` flow, which requires DAO-level coordination. The `migrate` function's unconditional carry-over of the flag means even a state migration does not fix it. [9](#0-8) 

---

### Recommendation

Add a privileged setter callable by `Role::DAO` that allows `skip_pow_verification` to be set to `false` after initialization:

```rust
pub fn set_skip_pow_verification(&mut self, value: bool) {
    // only callable by DAO role
    self.skip_pow_verification = value;
}
```

Alternatively, remove the flag entirely from production builds using a compile-time feature gate (`#[cfg(test)]`), so it can never appear in a deployed WASM binary. This mirrors the external report's recommendation to fetch sensitive material only when absolutely needed and clear it immediately after use.

---

### Proof of Concept

1. Deploy the contract with `InitArgs { skip_pow_verification: true, ... }`. The flag is written to state at line 142.
2. Confirm there is no method to change it — a full grep of the contract source shows no setter.
3. As a trusted relayer, call `submit_blocks` with a `BlockHeader` whose `nonce` is set to `0` (guaranteed invalid PoW for any real target).
4. In `submit_block_header`, the branch `if !skip_pow_verification` (line 517) evaluates to `false`; the `require!` at lines 522–525 is never reached.
5. The header is stored via `store_block_header` and `mainchain_tip_blockhash` is updated to the fabricated hash.
6. Construct a `ProofArgs` with a `merkle_proof` that hashes to the fabricated block's `merkle_root` (freely chosen by the attacker when crafting the fake header).
7. Call `verify_transaction_inclusion` — it returns `true` for a transaction that does not exist on the real Bitcoin chain. [10](#0-9)

### Citations

**File:** contract/src/lib.rs (L110-112)
```rust
    // If we should run all the block checks or not
    skip_pow_verification: bool,

```

**File:** contract/src/lib.rs (L130-130)
```rust
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
```

**File:** contract/src/lib.rs (L142-142)
```rust
            skip_pow_verification: args.skip_pow_verification,
```

**File:** contract/src/lib.rs (L177-179)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
```

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L517-526)
```rust
        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }
```

**File:** contract/src/lib.rs (L735-746)
```rust
            if let Ok(old_state) = BtcLightClientV2::try_from_slice(&raw_state) {
                log!("migrating state from the V2 layout");
                return Self {
                    mainchain_height_to_header: old_state.mainchain_height_to_header,
                    mainchain_header_to_height: old_state.mainchain_header_to_height,
                    mainchain_tip_blockhash: old_state.mainchain_tip_blockhash,
                    mainchain_initial_blockhash: old_state.mainchain_initial_blockhash,
                    headers_pool: old_state.headers_pool,
                    skip_pow_verification: old_state.skip_pow_verification,
                    gc_threshold: old_state.gc_threshold,
                    network: old_state.network,
                };
```

**File:** contract/src/dogecoin.rs (L150-154)
```rust
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
```
