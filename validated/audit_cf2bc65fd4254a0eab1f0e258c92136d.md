### Title
Equihash Validation Result `Ok(false)` Silently Discarded — Invalid Zcash PoW Headers Accepted - (File: `contract/src/zcash.rs`)

### Summary
The `check_pow` function for Zcash uses `.unwrap_or_else` on the `Result<bool, _>` returned by `equihash::is_valid_solution`, handling only the `Err` variant. The `Ok(false)` case — which signals a cryptographically invalid Equihash solution — is silently discarded. This is the direct Rust analog of the "missing await" class: a validation call whose result is not fully consumed, allowing the validation to be bypassed.

### Finding Description
In `contract/src/zcash.rs`, `check_pow` ends with:

```rust
equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
    .unwrap_or_else(|e| {
        env::panic_str(&format!("Invalid Equihash solution: {e}"));
    });
``` [1](#0-0) 

`equihash::is_valid_solution` returns `Result<bool, Error>`. The `.unwrap_or_else` closure fires only on `Err(_)`. When the function returns `Ok(false)` — meaning the solution was computed successfully but is cryptographically invalid — the `false` value is dropped and execution continues without any panic or rejection. The correct pattern is to additionally assert the inner `bool`:

```rust
require!(
    equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
        .unwrap_or_else(|e| env::panic_str(&format!("Invalid Equihash solution: {e}"))),
    "Invalid Equihash solution"
);
```

The call chain is: `submit_blocks` → `submit_block_header` (non-dogecoin path) → `check_target` → `check_pow` (zcash module). [2](#0-1) [3](#0-2) 

### Impact Explanation
A block header with a well-formed structure (valid `bits`, valid timestamp, valid version) but an invalid Equihash solution passes `check_pow` without rejection. The header is stored in `headers_pool`, assigned a `chain_work`, and — if its accumulated work exceeds the current tip — triggers `reorg_chain`, corrupting the canonical chain state. Downstream consumers calling `verify_transaction_inclusion_v2` against a block accepted via this path receive a forged inclusion proof rooted in a header that was never actually mined.

### Likelihood Explanation
The Zcash feature flag is a compile-time selection; on a Zcash deployment this code is the live PoW validation path. Any caller who can invoke `submit_blocks` (a registered relayer, or any account if the staking/registration is permissionless) can craft a header with an arbitrary fake Equihash solution and have it accepted.

### Recommendation
Wrap the call in a `require!` that asserts the returned `bool`:

```rust
require!(
    equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
        .unwrap_or_else(|e| env::panic_str(&format!("Invalid Equihash solution: {e}"))),
    "Invalid Equihash solution"
);
``` [4](#0-3) 

### Proof of Concept

1. Deploy the contract with the `zcash` feature flag.
2. Construct a `Header` whose `bits`, `time`, and `version` satisfy all `require!` checks in `check_pow`, but whose `solution` field is an arbitrary byte sequence that is not a valid Equihash(200,9) solution.
3. Call `submit_blocks([crafted_header])`.
4. Observe that `equihash::is_valid_solution` returns `Ok(false)`, the `.unwrap_or_else` closure does not fire, and the header is stored in `headers_pool` and promoted to the main chain tip.
5. Call `get_last_block_header()` — it returns the attacker-supplied header, confirming the corrupted canonical state. [1](#0-0) [5](#0-4)

### Citations

**File:** contract/src/zcash.rs (L59-67)
```rust
        // Check Equihash solution
        let n = 200;
        let k = 9;
        let input = block_header.get_block_header_vec_for_equihash();

        equihash::is_valid_solution(n, k, &input, &block_header.nonce.0, &block_header.solution)
            .unwrap_or_else(|e| {
                env::panic_str(&format!("Invalid Equihash solution: {e}"));
            });
```

**File:** contract/src/lib.rs (L169-198)
```rust
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

**File:** contract/src/lib.rs (L570-572)
```rust
    fn check_target(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        self.check_pow(block_header, prev_block_header);
    }
```
