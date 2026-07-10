### Title
Hardcoded Equihash Parameters Bypass Post-Upgrade Consensus Validation — (File: `contract/src/zcash.rs`)

---

### Summary

The Zcash `check_pow` function hardcodes Equihash parameters `n = 200` and `k = 9` directly in the function body rather than sourcing them from the configurable `ZcashConfig` struct. If Zcash activates a network upgrade that changes these parameters, the contract continues accepting blocks proven under the old Equihash instantiation, allowing an attacker to submit a fork chain that satisfies the stale proof rules while diverging from real Zcash consensus. This is the direct structural analog to H-01: a critical consensus value is assumed to be a permanent constant when it is in fact a protocol-level parameter that can change.

---

### Finding Description

In `contract/src/zcash.rs` lines 60–61, inside `check_pow`, the Equihash parameters are hardcoded as bare integer literals:

```rust
let n = 200;
let k = 9;
``` [1](#0-0) 

These values are then passed directly to the external `equihash::is_valid_solution` call, which is the sole cryptographic gate that distinguishes a valid Zcash block from a fabricated one.

The `ZcashConfig` struct in `btc-types/src/network.rs` is explicitly designed to hold all Zcash consensus parameters — `pow_averaging_window`, `post_blossom_pow_target_spacing`, `pow_max_adjust_down`, `pow_max_adjust_up`, `pow_allow_min_difficulty_blocks_after_height`, `pow_limit`, `proof_of_work_limit_bits` — yet `n` and `k` are absent from it entirely. [2](#0-1) 

The mainnet and testnet configs returned by `get_zcash_config` therefore have no field for these parameters, and there is no upgrade path to change them without a full contract code redeployment. [3](#0-2) 

---

### Impact Explanation

`check_pow` is called from `submit_block_header` for every block submitted when `skip_pow_verification = false` (the production setting). [4](#0-3) 

If Zcash activates a network upgrade that changes the Equihash instantiation (e.g., to `n = 144, k = 5` as used in Zcash Testnet variants or future hard forks), two failure modes arise:

1. **Fork-chain acceptance (security impact):** An attacker who continues mining on the pre-upgrade Equihash instantiation can submit a fork chain to `submit_blocks()`. The contract validates each block's Equihash solution against the old `(n=200, k=9)` parameters and accepts it. If the attacker's fork accumulates more `chain_work` than the real post-upgrade chain (which the contract cannot track because it rejects all post-upgrade blocks), `reorg_chain` promotes the attacker's fork to `mainchain_tip_blockhash`. Every subsequent call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` by a consumer contract then operates against a fabricated chain, returning `true` for transactions that never occurred on the real Zcash network. [5](#0-4) 

2. **Permanent chain freeze (DoS — noted for completeness, not the primary impact):** All post-upgrade blocks fail Equihash validation and are rejected, freezing the light client at the last pre-upgrade block.

The primary security impact is (1): the contract's canonical chain can be replaced by an attacker-controlled fork, corrupting the `mainchain_tip_blockhash` state variable and invalidating all downstream transaction inclusion proofs.

---

### Likelihood Explanation

Zcash has used `n = 200, k = 9` since genesis and has not changed it. However:

- Zcash's upgrade cadence (Sapling, Blossom, Heartwood, Canopy, NU5) demonstrates that consensus parameters are actively modified.
- The Equihash parameters are explicitly a protocol-level choice, not a physical constant.
- The structural absence of `n` and `k` from `ZcashConfig` means there is no governance path to update them short of a full contract upgrade, creating a window of vulnerability between a Zcash network upgrade announcement and a contract redeployment.

Likelihood is low but non-zero — identical in character to the H-01 assessment that stablecoin depeg probability is "low, but not zero."

---

### Recommendation

Add `equihash_n: u32` and `equihash_k: u32` fields to `ZcashConfig` in `btc-types/src/network.rs`, populate them in `get_zcash_config`, and replace the hardcoded literals in `check_pow` with `config.equihash_n` and `config.equihash_k`. This mirrors how every other Zcash consensus parameter is already handled and allows the values to be updated via the existing contract upgrade mechanism without changing validation logic.

---

### Proof of Concept

1. Zcash activates a network upgrade at height `H` that changes Equihash to `(n=144, k=5)`.
2. The real Zcash chain advances past height `H` using the new parameters; the contract rejects all these blocks because `equihash::is_valid_solution(200, 9, ...)` fails on them.
3. An attacker continues mining a fork from height `H-1` using the old `(n=200, k=9)` parameters and calls `submit_blocks()` on NEAR with these headers.
4. `check_pow` in `contract/src/zcash.rs` validates each header's Equihash solution with the hardcoded `n=200, k=9` — the solutions are valid, so the blocks are accepted.
5. Because the real chain is frozen at `H-1` from the contract's perspective, the attacker's fork eventually exceeds the stored `chain_work` of the tip, triggering `reorg_chain` and setting `mainchain_tip_blockhash` to the attacker's fork tip.
6. A consumer contract calls `verify_transaction_inclusion_v2` for a transaction that exists only on the attacker's fork; the contract returns `true`, deceiving the consumer. [6](#0-5) [2](#0-1)

### Citations

**File:** contract/src/zcash.rs (L59-68)
```rust
        // Check Equihash solution
        let n = 200;
        let k = 9;
        let input = block_header.get_block_header_vec_for_equihash();

        equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
            .unwrap_or_else(|e| {
                env::panic_str(&format!("Invalid Equihash solution: {e}"));
            });
    }
```

**File:** btc-types/src/network.rs (L109-150)
```rust
pub fn get_zcash_config(network: Network) -> ZcashConfig {
    match network {
        Network::Mainnet => ZcashConfig {
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L288
            proof_of_work_limit_bits: 0x1f07ffff,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L103
            pow_limit: U256::new(
                0x0007_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
                0xffff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
            ),
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L104
            pow_averaging_window: 17,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/consensus/params.h#L244
            post_blossom_pow_target_spacing: 75,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L429
            pow_max_adjust_down: 32, // 32% adjustment down
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L430
            pow_max_adjust_up: 16, // 16% adjustment up
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L110
            pow_allow_min_difficulty_blocks_after_height: None,
        },
        Network::Testnet => ZcashConfig {
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L629
            proof_of_work_limit_bits: 0x2007ffff,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L426
            pow_limit: U256::new(
                0x07ff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
                0xffff_ffff_ffff_ffff_ffff_ffff_ffff_ffff,
            ),
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L427
            pow_averaging_window: 17,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/consensus/params.h#L244
            post_blossom_pow_target_spacing: 75,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L429
            pow_max_adjust_down: 32,
            //https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L430
            pow_max_adjust_up: 16,
            // https://github.com/zcash/zcash/blob/2352fbc1ed650ac4369006bea11f7f20ee046b84/src/chainparams.cpp#L433
            pow_allow_min_difficulty_blocks_after_height: Some(299187),
        },
    }
}
```

**File:** btc-types/src/network.rs (L176-186)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Copy, Debug)]
pub struct ZcashConfig {
    pub proof_of_work_limit_bits: u32,
    pub pow_limit: U256,
    pub pow_averaging_window: i64,
    pub post_blossom_pow_target_spacing: i64,
    pub pow_max_adjust_down: i64,
    pub pow_max_adjust_up: i64,
    pub pow_allow_min_difficulty_blocks_after_height: Option<u64>,
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

**File:** contract/src/lib.rs (L560-567)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```
