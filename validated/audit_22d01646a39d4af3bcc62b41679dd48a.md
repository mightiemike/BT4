### Title
Unpermissioned `run_mainchain_gc()` Allows Any Caller to Prune Canonical Chain Headers - (File: `contract/src/lib.rs`)

### Summary
`run_mainchain_gc()` is a public, state-mutating NEAR contract method that carries only a `#[pause]` decorator. The `#[pause]` macro exclusively gates calls when the contract is in a paused state; it imposes no caller restriction when the contract is running normally. Any unprivileged NEAR account can therefore invoke `run_mainchain_gc(u64::MAX)` at will, forcing the maximum possible removal of canonical-chain block headers in a single transaction.

### Finding Description
The function is declared as:

```rust
#[pause(except(roles(Role::UnrestrictedRunGC)))]
pub fn run_mainchain_gc(&mut self, batch_size: u64) {
``` [1](#0-0) 

The `#[pause]` attribute only prevents calls from non-`UnrestrictedRunGC` accounts when the contract is paused. When the contract is live (the normal operating state), the attribute is a no-op with respect to caller identity. There is no `#[trusted_relayer]`, `#[private]`, or any other role check that would restrict who may call this function.

Inside the function, the caller-supplied `batch_size` is used directly to compute how many of the oldest mainchain headers to delete:

```rust
let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
``` [2](#0-1) 

The loop then removes each eligible header from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`, and advances `mainchain_initial_blockhash`:

```rust
self.remove_block_header(blockhash);
self.mainchain_height_to_header.remove(&height);
...
self.mainchain_initial_blockhash = self.mainchain_height_to_header.get(&end_removal_height)...;
``` [3](#0-2) 

By contrast, `submit_blocks()` — the intended privileged path that also calls `run_mainchain_gc` internally — is correctly gated behind `#[trusted_relayer]`:

```rust
#[payable]
#[pause]
#[trusted_relayer]
pub fn submit_blocks(...)
``` [4](#0-3) 

`run_mainchain_gc` was clearly intended to be a companion to `submit_blocks`, but its standalone public exposure lacks the same guard.

### Impact Explanation
An attacker calling `run_mainchain_gc(u64::MAX)` forces the contract to delete every block header that exceeds `gc_threshold` in a single transaction. This permanently advances `mainchain_initial_blockhash` and removes the corresponding entries from all three storage maps. Any subsequent call to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction whose containing block was pruned will panic with "block does not belong to the current main chain", because `mainchain_header_to_height` no longer contains that block hash:

```rust
let target_block_height = self
    .mainchain_header_to_height
    .get(&args.tx_block_blockhash)
    .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));
``` [5](#0-4) 

The corrupted state is: `mainchain_initial_blockhash` is advanced beyond its legitimate position, and the canonical-chain height-to-hash and hash-to-height mappings are permanently truncated. SPV proofs for any transaction in a pruned block are permanently invalidated.

### Likelihood Explanation
The function is unconditionally reachable by any NEAR account with no deposit, no staking, and no role requirement when the contract is not paused. The contract is expected to be live (unpaused) during normal operation. The call requires only knowledge of the contract address and the function name, both of which are public. Likelihood is high.

### Recommendation
Add a role-based access control guard to `run_mainchain_gc` that mirrors the protection on `submit_blocks`. The simplest fix is to add `#[trusted_relayer]` or an explicit `acl_is_caller_role` check so that only accounts holding `Role::DAO`, `Role::RelayerManager`, or `Role::UnrestrictedRunGC` may invoke the function directly. Alternatively, make the function `pub(crate)` and remove it from the public NEAR ABI entirely, since its only legitimate caller is `submit_blocks`.

### Proof of Concept
1. Deploy the contract normally (not paused).
2. From any arbitrary NEAR account (no roles granted), call:
   ```
   run_mainchain_gc(18446744073709551615)
   ```
3. The contract removes all block headers beyond `gc_threshold` from `headers_pool`, `mainchain_height_to_header`, and `mainchain_header_to_height`, and advances `mainchain_initial_blockhash`.
4. Any subsequent `verify_transaction_inclusion` call referencing a pruned block hash panics with "block does not belong to the current main chain", permanently breaking SPV verification for those blocks.

### Citations

**File:** contract/src/lib.rs (L166-169)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
```

**File:** contract/src/lib.rs (L299-302)
```rust
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

```

**File:** contract/src/lib.rs (L376-377)
```rust
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
```

**File:** contract/src/lib.rs (L393-393)
```rust
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);
```

**File:** contract/src/lib.rs (L407-414)
```rust
                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
```
