### Title
Unprivileged Caller Can Bulk-Delete Mainchain Block Headers via `run_mainchain_gc` - (File: `contract/src/lib.rs`)

---

### Summary
`run_mainchain_gc` is a public, unguarded state-mutating function. Any unprivileged NEAR account can call it with an arbitrarily large `batch_size`, immediately removing every GC-eligible block header from the mainchain in a single transaction. This is the direct analog of H-01: just as anyone could call `burnObject(address from, …)` to destroy NFTs belonging to another user, anyone can call `run_mainchain_gc(u64::MAX)` to destroy block headers that were submitted by trusted relayers, without holding any privileged role.

---

### Finding Description

`submit_blocks` — the only intended path for advancing the chain — is gated behind `#[trusted_relayer]`, which enforces that only staked, whitelisted relayers may submit headers. [1](#0-0) 

After each batch submission, `submit_blocks` internally calls `run_mainchain_gc(num_of_headers)`, where `num_of_headers` is the small count of headers just submitted, deliberately throttling how many old headers are pruned per call. [2](#0-1) 

`run_mainchain_gc` itself, however, carries only a `#[pause]` decorator — which restricts callers only when the contract is **paused**. When the contract is live (the normal operating state), the function is callable by any NEAR account with no role check, no deposit, and no staking requirement: [3](#0-2) 

Inside the function, the number of headers actually removed is bounded by `total_amount_to_remove = amount_of_headers_we_store - gc_threshold`, but the caller-supplied `batch_size` controls how much of that budget is consumed in one call: [4](#0-3) 

By passing `batch_size = u64::MAX`, an attacker exhausts the entire GC budget in a single transaction, removing every header that has grown beyond `gc_threshold`. For each removed height the function deletes the entry from `mainchain_height_to_header`, calls `remove_block_header` (which also removes from `mainchain_header_to_height` and `headers_pool`), and advances `mainchain_initial_blockhash`: [5](#0-4) 

The `remove_block_header` helper permanently erases both the height-to-hash index and the header itself from the pool: [6](#0-5) 

---

### Impact Explanation

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` look up the target block in `mainchain_header_to_height` and `headers_pool`. Once a header is removed by the attacker-triggered GC, both lookups panic or return "block does not belong to the current main chain": [7](#0-6) 

Any downstream contract or user that submitted an SPV proof for a transaction in a now-deleted block receives a hard failure. The deleted state is permanent — there is no re-insertion path. The attacker effectively destroys the light-client's historical record for all blocks that had grown beyond `gc_threshold`, invalidating all in-flight and future SPV proofs for those heights.

---

### Likelihood Explanation

- No privileged role, staking, or deposit is required to call `run_mainchain_gc`.
- The contract is expected to run unpaused in production; the `#[pause]` guard is irrelevant under normal conditions.
- A single NEAR transaction suffices; the call is free beyond gas.
- Any observer of the NEAR chain can detect when `amount_of_headers_we_store > gc_threshold` and immediately exploit the window.

Likelihood: **High**.

---

### Recommendation

Apply the same `#[trusted_relayer]` guard (or at minimum a role-based `acl_require` check) to `run_mainchain_gc` that is already applied to `submit_blocks`. If the function must remain publicly callable for operational reasons, cap `batch_size` to a small protocol-defined constant and add a role check:

```rust
// Option A – restrict to trusted relayers only
#[pause(except(roles(Role::UnrestrictedRunGC)))]
#[trusted_relayer]
pub fn run_mainchain_gc(&mut self, batch_size: u64) { … }

// Option B – add an explicit role guard and cap batch_size
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
    require!(
        self.acl_has_role(Role::UnrestrictedRunGC, &env::predecessor_account_id())
            || /* trusted relayer check */,
        "Unauthorized"
    );
    let batch_size = batch_size.min(MAX_GC_BATCH_SIZE);
    …
}
```

---

### Proof of Concept

1. Trusted relayer submits enough blocks that `amount_of_headers_we_store > gc_threshold` (e.g., `gc_threshold = 52704`, chain grows to 52800 blocks → 96 headers are GC-eligible).
2. A user submits an SPV proof for a transaction in block at height `mainchain_initial_height + 50` (within the GC-eligible window). The proof is pending cross-contract callback resolution.
3. Attacker (any NEAR account) calls:
   ```
   run_mainchain_gc(batch_size: 18446744073709551615)
   ```
4. The function removes all 96 GC-eligible headers, advances `mainchain_initial_blockhash` past height `mainchain_initial_height + 96`, and permanently deletes those entries from `headers_pool` and `mainchain_header_to_height`.
5. The user's SPV proof call now panics at `mainchain_header_to_height.get(&args.tx_block_blockhash)` with "block does not belong to the current main chain" — the proof is irreversibly invalidated. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L166-172)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
```

**File:** contract/src/lib.rs (L177-181)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L376-416)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
    }
```

**File:** contract/src/lib.rs (L659-662)
```rust
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```
