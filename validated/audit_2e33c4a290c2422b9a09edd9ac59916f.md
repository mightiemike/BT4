### Title
Fork Difficulty Retarget Uses Mainchain Ancestor Timestamp Instead of Fork Ancestor Timestamp - (`contract/src/bitcoin.rs`, `contract/src/litecoin.rs`, `contract/src/dogecoin.rs`)

---

### Summary

When a fork block falls on a difficulty-adjustment boundary, `get_next_work_required` fetches the retarget-period start block via `blocks_getter.get_header_by_height(first_block_height)`. That helper resolves exclusively from `mainchain_height_to_header`. If the fork diverged inside the current retarget window, the mainchain block at that height is a *different block* from the fork's true ancestor at the same height. The difficulty computed for the fork block is therefore based on the wrong timestamp, allowing an attacker who controls fork-block timestamps to shift the calculated target in their favour and submit fork blocks at artificially reduced difficulty.

The Dogecoin implementation even carries an explicit developer acknowledgement of this uncertainty:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
```

---

### Finding Description

**Root cause — `get_header_by_height` is mainchain-only**

`BtcLightClient::get_header_by_height` (the `BlocksGetter` implementation) reads only from `mainchain_height_to_header`:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

**How it is used in difficulty calculation (Bitcoin path)**

```rust
// contract/src/bitcoin.rs  get_next_work_required()
let first_block_height =
    prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

let interval_tail_extend_header =
    blocks_getter.get_header_by_height(first_block_height);   // ← mainchain block

calculate_next_work_required(
    config,
    prev_block_header,
    interval_tail_extend_header.block_header.time.into(),     // ← mainchain timestamp
)
```

The same pattern appears verbatim in `litecoin.rs` and `dogecoin.rs`.

**The discrepancy**

Suppose the mainchain has blocks at heights 0–4031 and a fork diverges at height 2000. The fork's own blocks occupy heights 2001–4031 in `headers_pool` but are *not* in `mainchain_height_to_header`. When the fork block at height 4032 (a retarget boundary) is validated:

- `first_block_height` = 4032 − 2015 = **2017**
- `get_header_by_height(2017)` returns the **mainchain** block at 2017
- The fork's block at height 2017 is a *different block* with a *different timestamp*

`calculate_next_work_required` computes:

```
actual_time_taken = prev_fork_block_time − mainchain_block_2017_time
new_target        = old_target × actual_time_taken / pow_target_timespan
```

Because `mainchain_block_2017_time` is substituted for the fork's true ancestor timestamp, the attacker can craft the fork's block at height 2017 to carry a timestamp that is later than the mainchain's block at that height. This makes `actual_time_taken` larger than it should be, inflating `new_target` (lowering difficulty). The 4× clamp is the only bound.

**Entry path**

Any caller of `submit_blocks` (a trusted relayer, or an account with `Role::UnrestrictedSubmitBlocks`) can supply a batch of adversarial fork headers. `submit_block_header` calls `check_target` → `check_pow` → `get_next_work_required` for every header where `skip_pow_verification` is `false` (the production setting).

---

### Impact Explanation

An attacker who controls fork-block timestamps can cause the contract to accept fork blocks at a lower difficulty than the fork's actual history requires. With reduced per-block work, the fork accumulates `chain_work` faster. Once `current_header.chain_work > total_main_chain_chainwork`, `reorg_chain` is triggered, replacing the canonical chain with the attacker's fork. Downstream consumers of `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` will then verify proofs against the attacker's chain, returning `true` for transactions that were never confirmed on the real Bitcoin network.

---

### Likelihood Explanation

The preconditions are:
1. A fork that diverges *within* the current retarget window (heights between the last retarget boundary and the next one) — a normal occurrence during any chain reorganization.
2. The attacker controls the fork's block at the retarget-window start height and can set its timestamp up to `MAX_FUTURE_BLOCK_TIME_LOCAL` (2 hours) ahead of the real time, which is within the allowed range enforced by `check_pow`.
3. The attacker has relayer access (trusted relayer role or `UnrestrictedSubmitBlocks`).

Condition 3 limits the open attack surface, but a compromised or malicious relayer — the exact actor the system is designed to accept data from — satisfies it trivially.

---

### Recommendation

Replace the mainchain-only `get_header_by_height` lookup with an ancestor traversal that walks the fork's own `prev_block_hash` chain back to the retarget boundary. Store the retarget-boundary block hash inside `ExtendedHeader` at submission time, or walk `headers_pool` via `prev_block_hash` links from `prev_block_header` until the target height is reached. This mirrors how Bitcoin Core resolves the ancestor: it walks the chain tip's ancestor list, not a height-indexed map of the current best chain.

---

### Proof of Concept

```
Setup
─────
Mainchain (submitted by honest relayer):
  height 2016 → block M_2016  (timestamp T_m)
  height 4031 → block M_4031

Attacker submits fork diverging at height 2000:
  height 2001–4031 → fork blocks F_2001 … F_4031
  F_2017.time = T_m + 7000   (within 2-hour future-time allowance)

Attack
──────
Attacker submits fork block at height 4032 (retarget boundary).

Contract calls get_next_work_required:
  first_block_height = 4032 − 2015 = 2017
  get_header_by_height(2017) → M_2017  (mainchain block, timestamp T_m)

  actual_time_taken = F_4031.time − T_m          ← uses mainchain timestamp
  correct value     = F_4031.time − F_2017.time  ← should use fork ancestor

  Since F_2017.time = T_m + 7000:
    actual_time_taken (wrong) > correct actual_time_taken by 7000 seconds
    new_target (wrong) > correct new_target  →  lower difficulty

Fork block at height 4032 is accepted with lower difficulty.
Attacker mines subsequent fork blocks faster, accumulates chain_work,
triggers reorg_chain, corrupts canonical chain.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/bitcoin.rs (L78-87)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
```

**File:** contract/src/bitcoin.rs (L90-117)
```rust
fn calculate_next_work_required(
    config: &NetworkConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let prev_block_time: i64 = prev_block_header.block_header.time.into();

    let mut actual_time_taken: i64 = prev_block_time - first_block_time;
    if actual_time_taken < config.pow_target_timespan / 4 {
        actual_time_taken = config.pow_target_timespan / 4;
    }
    if actual_time_taken > config.pow_target_timespan * 4 {
        actual_time_taken = config.pow_target_timespan * 4;
    }

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(actual_time_taken).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target = new_target
        / U256::from(<i64 as TryInto<u64>>::try_into(config.pow_target_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
```

**File:** contract/src/litecoin.rs (L86-93)
```rust
    let first_block_height = prev_block_header.block_height - blocks_to_go_back;

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/lib.rs (L562-567)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```
