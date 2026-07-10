### Title
Unchecked Boolean Return Value from Equihash Validity Check Allows Invalid PoW Block Acceptance - (File: `contract/src/zcash.rs`)

### Summary
`equihash::is_valid_solution` returns `Result<bool, Error>`. The contract handles the `Err` variant by panicking, but silently discards the inner `bool` from the `Ok` variant. If the library returns `Ok(false)` — meaning the solution is cryptographically invalid — the contract does not panic and does not reject the block. Any unprivileged relayer or NEAR caller can submit a Zcash block with a forged Equihash solution that passes all other checks, and the contract will accept and store it as a valid mainchain header.

### Finding Description
In `contract/src/zcash.rs`, the `check_pow` function validates the Equihash solution as follows:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
```

`equihash::is_valid_solution` has the signature `fn(...) -> Result<bool, Error>`. The call chain is:
- `Err(e)` → closure panics (correct)
- `Ok(false)` → `unwrap_or_else` extracts `false`, the statement ends, **the value is dropped, no check occurs**
- `Ok(true)` → `unwrap_or_else` extracts `true`, the statement ends, value is dropped

The returned `bool` is never bound to a variable or compared against `true`. Rust does not warn on this because `bool` is not `#[must_use]` in this context. The net effect is that the entire Equihash validity check is a no-op for the `Ok(false)` case. [1](#0-0) 

### Impact Explanation
An attacker submitting a Zcash block with a structurally well-formed but cryptographically invalid Equihash solution will have that block accepted into the contract's `headers_pool` and potentially promoted to the mainchain tip. This corrupts the canonical chain state: `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height` will all reflect a block that does not satisfy Zcash's PoW consensus rules. Downstream consumers calling `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will receive results anchored to a fraudulent chain. [2](#0-1) 

### Likelihood Explanation
The entry point is the public `submit_blocks` function, callable by any NEAR account (subject to the `trusted_relayer` gate, but the `trusted_relayer` macro only enforces staking/registration, not cryptographic identity). A registered relayer — or any account if the bypass role is granted — can craft a Zcash header with an arbitrary `solution` field. The `equihash` crate's `is_valid_solution` is expected to return `Ok(false)` for invalid solutions rather than `Err`, making this a realistic trigger path. [3](#0-2) [4](#0-3) 

### Recommendation
Bind the result and assert it is `true`:

```rust
let is_valid = equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
require!(is_valid, "Invalid Equihash solution: validation returned false");
```

### Proof of Concept
1. Compile the contract with `--features zcash`.
2. Initialize the contract with a valid Zcash genesis sequence.
3. Construct a `Header` whose `solution` field is all-zeros (or any bytes that are structurally decodable but cryptographically invalid for the given `n=200, k=9` parameters).
4. Call `submit_blocks` with this header (all other fields — `bits`, `time`, `version` — set to pass the surrounding `require!` checks).
5. Observe that `equihash::is_valid_solution` returns `Ok(false)`, the `unwrap_or_else` closure is not invoked, no panic occurs, and the block is stored via `store_block_header` / `store_fork_header`.
6. Query `get_last_block_header` to confirm the invalid block is now the mainchain tip. [5](#0-4) [6](#0-5)

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

**File:** contract/src/lib.rs (L166-198)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L531-568)
```rust
    fn submit_block_header_inner(
        &mut self,
        current_header: ExtendedHeader,
        prev_block_header: &ExtendedHeader,
    ) {
        // Main chain submission
        if prev_block_header.block_hash == self.mainchain_tip_blockhash {
            // Probably we should check if it is not in a mainchain?
            // chainwork > highScore
            log!("Block {}: saving to mainchain", current_header.block_hash);
            // Validate chain
            assert_eq!(
                self.mainchain_tip_blockhash,
                current_header.block_header.prev_block_hash
            );

            self.store_block_header(&current_header);
            self.mainchain_tip_blockhash = current_header.block_hash;
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
    }
```

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
    }
```
