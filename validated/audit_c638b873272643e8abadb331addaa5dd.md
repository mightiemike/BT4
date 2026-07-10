### Title
`verify_transaction_inclusion_v2` Inherits Deprecated v1's `#[pause]` Guard, Blocking the Recommended SPV Verification Path When v1 Is Paused — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` delegates its final verification step to the deprecated `verify_transaction_inclusion` via an internal Rust method call. Because `verify_transaction_inclusion` carries its own `#[pause]` attribute, pausing that deprecated feature also silently disables `verify_transaction_inclusion_v2`, even though v2 has its own independent `#[pause]` guard and is not paused. This is the direct analog of M-07: a guard intended to block one operation (direct calls to the deprecated v1 endpoint) accidentally blocks a different, intended operation (the recommended v2 endpoint).

### Finding Description

`verify_transaction_inclusion_v2` is the recommended, security-hardened replacement for the deprecated `verify_transaction_inclusion`. It adds a coinbase Merkle proof check to mitigate the 64-byte transaction forgery vulnerability, then delegates to v1 for the remaining checks:

```rust
// contract/src/lib.rs:346-368
#[pause]
pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
    // ... coinbase proof validation ...
    #[allow(deprecated)]
    self.verify_transaction_inclusion(args.into())   // ← internal Rust call
}
```

The deprecated v1 function carries its own `#[pause]` attribute:

```rust
// contract/src/lib.rs:283-288
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool { ... }
```

The `near_plugins` `Pausable` trait supports per-feature pausing by function name. When a `PauseManager` calls `pa_pause_feature("verify_transaction_inclusion")` — the natural, expected action to enforce migration away from the deprecated endpoint — the `#[pause]` procedural macro wrapping `verify_transaction_inclusion`'s body fires on every call to that method, including the internal Rust call at line 368. The v2 function's own `#[pause]` guard passes (v2 is not paused), but execution panics inside the delegated v1 call.

The result: pausing the deprecated v1 endpoint simultaneously and silently disables the recommended v2 endpoint, breaking all SPV proof verification for every downstream consumer.

### Impact Explanation

Any NEAR account or smart contract that calls `verify_transaction_inclusion_v2` to verify Bitcoin transaction inclusion receives a panic instead of a proof result. Downstream bridge contracts, payment verifiers, or custody systems that rely on this function to gate asset releases or settlement decisions are completely blocked. The intended migration path (deprecate v1, force users to v2) is self-defeating: the act of enforcing migration destroys the replacement endpoint.

### Likelihood Explanation

The `PauseManager` role exists precisely to pause individual features. Pausing `verify_transaction_inclusion` is the documented, expected mechanism to deprecate it. The role holder has no reason to know that doing so also disables v2, because v2 has its own `#[pause]` guard that visually implies it is independently controlled. This is a realistic operational action with no warning in the code or documentation.

### Recommendation

Remove the `#[pause]` attribute from `verify_transaction_inclusion`. The deprecated function is already guarded by v2's `#[pause]` when called through the recommended path, and direct external calls to v1 can be blocked by removing or restricting the public API surface rather than by a pause guard that bleeds into v2. Alternatively, extract the shared inner logic into a private helper function (without `#[pause]`) and have both v1 and v2 call that helper, so each public endpoint's pause state is fully independent.

```diff
- #[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
- #[pause]
- pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }
+ #[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
+ pub fn verify_transaction_inclusion(&self, ...) -> bool {
+     self.verify_transaction_inclusion_inner(args)
+ }

+ fn verify_transaction_inclusion_inner(&self, args: ProofArgs) -> bool { ... }

  #[pause]
  pub fn verify_transaction_inclusion_v2(&self, ...) -> bool {
      // ... coinbase proof ...
-     self.verify_transaction_inclusion(args.into())
+     self.verify_transaction_inclusion_inner(args.into())
  }
```

### Proof of Concept

1. Deploy the contract (any chain feature).
2. Grant an account the `PauseManager` role.
3. Call `pa_pause_feature("verify_transaction_inclusion")` from the `PauseManager` account to deprecate the v1 endpoint.
4. Submit a valid block header and attempt to call `verify_transaction_inclusion_v2` with a correct coinbase + transaction Merkle proof.
5. Observe: the call panics with the `#[pause]` error from inside `verify_transaction_inclusion`, even though `verify_transaction_inclusion_v2` itself is not paused.

The root cause is at: [1](#0-0) [2](#0-1)

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L346-368)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```
