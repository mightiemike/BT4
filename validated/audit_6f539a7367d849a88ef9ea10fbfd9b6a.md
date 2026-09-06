### Title
Missing bitvec-length validation lets `check_pox_bitvector` silently accept a wrongly-rewarded PoX punishment address - (File: `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
`NakamotoChainState::check_pox_bitvector` is the block-validation routine that checks a Nakamoto block header's self-reported PoX `bitvec` against the ground-truth reward/punish `treatment` recorded in the tenure's `LeaderBlockCommitOp` (parsed from the Bitcoin commit transaction). Analogous to the Elfi report — where a required piece of state (`setOraclePrice`) was never set, so a downstream `==0` check produced a wrong outcome instead of the intended behavior — here a required piece of state (the reward-set-sized `bitvec`) can simply be omitted/truncated by the block producer, and the missing entries are defaulted rather than treated as failing validation, silently flipping the outcome of an equality check that is supposed to gate reward legitimacy.

### Finding Description
`check_pox_bitvector` computes, for each `treated_addr` in `tenure_block_commit.treatment`, the bit values at that address's indices in the block header's `pox_treatment` bitvector: [1](#0-0) 

Crucially, `block_bitvec.get(ix)` returns `None` whenever `ix >= block_bitvec.len()` [2](#0-1) , and the `BitVec` codec places **no constraint that the deserialized length matches the reward-set size** — any `len` between 1 and `MAX_SIZE` (4000) is accepted on deserialize [3](#0-2) . A block producer therefore fully controls how short the header's `pox_treatment` bitvec is.

When `get(ix)` returns `None` (i.e., the header's bitvec is shorter than the reward set), `check_pox_bitvector` defaults the bit to `true`: [4](#0-3) 

The subsequent logic then evaluates: [5](#0-4) 

This asymmetry is the bug: defaulting missing bits to `true` correctly catches an invalid *punishment* (`all_1` && `is_punish()` → error), but it silently passes an invalid *reward* claim. If the ground-truth `treatment` marks an address as rewarded (`is_reward() == true`) while the "true" bit for that address should have been `0` (i.e., the address should have been punished), a miner can simply publish a header `bitvec` short enough to omit that address's index. `get(ix)` then returns `None`, defaults to `true`, `all_1` becomes true, and the `else if all_0 && treated_addr.is_reward()` branch is never reached — no error is raised, and the block is accepted as valid.

Because this validation is deterministic given the block header, the tenure's Bitcoin-verified `treatment`, and the active reward set, every honest node that processes the block executes exactly the same defaulting logic and reaches the same (wrong) "valid" verdict — this is a network-wide acceptance of a block whose recorded PoX-reward/punish accounting does not match the required consistency invariant, not merely a local bug.

### Impact Explanation
This breaks the intended equality "declared bitvec bit for index *i* == the actual reward/punish disposition of the PoX address at index *i*" for any index beyond the length the block producer chooses to publish. It allows a single, unprivileged block producer (the current tenure's miner) to have an on-chain block accepted network-wide that falsely legitimizes a reward claim that should have been flagged/rejected as an invalid punishment-vs-reward mismatch. This is bounded to the PoX reward/punishment bookkeeping recorded in the Nakamoto block header (consumed by signers/`.pox-*`/`get-burn-block-info?` consumers), matching the "reward paid...to the wrong party" / "invalid block accepted...network-wide" category, and is a minority (single miner)-triggerable, unprivileged action requiring no majority collusion.

### Likelihood Explanation
Any block producer fully controls the `pox_treatment` field of its own Nakamoto block header and can freely choose its length (1 to 4000) independent of the active reward set's size, since there is no cross-check enforcing `bitvec.len() == active_reward_set.rewarded_addresses().len()` prior to or within `check_pox_bitvector`. Triggering the flaw only requires crafting a short bitvec, which is trivially reachable by the block's own author without any additional signer or majority cooperation.

### Recommendation
In `check_pox_bitvector`, treat an out-of-range `block_bitvec.get(ix)` as a hard validation failure (return `Err(ChainstateError::InvalidStacksBlock(...))`) rather than defaulting to `true`, or explicitly require and verify that `block_bitvec.len() >= rewarded_addresses.len()` before performing the per-address checks, so that every PoX address in the active reward set must have an explicit, checkable bit.

### Proof of Concept
1. A miner's Bitcoin block-commit contains `treatment` entries where some `PoxAddress` is marked `is_reward()` even though, per the reward set's true PoX-punishment state, that address's bit should be `0`.
2. The miner crafts the corresponding Nakamoto block header's `pox_treatment` `BitVec` with `len` shorter than that address's index in `rewarded_addresses` (allowed since `consensus_deserialize`/`try_from` only enforce `0 < len <= MAX_SIZE`, not equality with the reward-set size) — see `stacks-common/src/bitvec.rs:41-91`.
3. On block validation, `check_pox_bitvector` calls `block_bitvec.get(ix)` for that address's index, which returns `None` (index >= `len`) and is defaulted to `true` at `stackslib/src/chainstate/nakamoto/mod.rs:4930-4934`.
4. `all_1` becomes `true`; since `treated_addr.is_punish()` is false (it's a reward claim) the punishment-error branch is skipped, and the `all_0`-guarded reward-error branch never fires because `all_1` (not `all_0`). The block passes `check_pox_bitvector` and is accepted, network-wide, despite the reward claim not being backed by a legitimate `1` bit in the header.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L4924-4938)
```rust
            let bitvec_values: Result<Vec<_>, ChainstateError> = address_indices
                .iter()
                .map(
                    |ix| {
                        let ix = u16::try_from(*ix)
                            .map_err(|_| ChainstateError::InvalidStacksBlock("Reward set index outside of u16".into()))?;
                        let bitvec_value = block_bitvec.get(ix)
                            .unwrap_or_else(|| {
                                warn!("Block header's bitvec is smaller than the reward set, defaulting higher indexes to 1");
                                true
                            });
                        Ok(bitvec_value)
                    }
                )
                .collect();
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L4940-4962)
```rust
            let all_1 = bitvec_values.iter().all(|x| *x);
            let all_0 = bitvec_values.iter().all(|x| !x);
            if all_1 {
                if treated_addr.is_punish() {
                    warn!(
                        "Invalid Nakamoto block: punished PoX address when bitvec contained 1s for the address";
                        "reward_address" => %treated_addr.deref(),
                        "bitvec_values" => ?bitvec_values,
                    );
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Bitvec does not match the block commit's PoX handling".into(),
                    ));
                }
            } else if all_0 && treated_addr.is_reward() {
                warn!(
                    "Invalid Nakamoto block: rewarded PoX address when bitvec contained 0s for the address";
                    "reward_address" => %treated_addr.deref(),
                    "bitvec_values" => ?bitvec_values,
                );
                return Err(ChainstateError::InvalidStacksBlock(
                    "Bitvec does not match the block commit's PoX handling".into(),
                ));
            }
```

**File:** stacks-common/src/bitvec.rs (L76-91)
```rust
    fn consensus_deserialize<R: std::io::Read>(fd: &mut R) -> Result<Self, CodecError> {
        let len = read_next(fd)?;
        if len == 0 {
            return Err(CodecError::DeserializeError(
                "BitVec lengths must be positive".to_string(),
            ));
        }
        if len > MAX_SIZE {
            return Err(CodecError::DeserializeError(format!(
                "BitVec length exceeded maximum. Max size = {MAX_SIZE}, len = {len}"
            )));
        }

        let data = read_next_exact(fd, Self::data_len(len).into())?;
        Ok(BitVec { data, len })
    }
```

**File:** stacks-common/src/bitvec.rs (L201-209)
```rust
    pub fn get(&self, i: u16) -> Option<bool> {
        if i >= self.len {
            return None;
        }
        let vec_index = usize::from(i / 8);
        let byte = self.data.get(vec_index)?;
        let bit_index = Self::bit_index(i);
        Some((*byte & bit_index) != 0)
    }
```
