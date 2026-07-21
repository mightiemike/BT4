### Title
Proof-version gate accepts `PROOF_VERSION_V0` in Rust pre-validation but Cairo OS hard-rejects it, creating an execution-layer revert for admitted transactions — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

### Summary

The Rust blockifier's `validate_proof_facts` pre-validation gate checks `allowed_proof_versions` from `VersionedConstants`. From protocol version V0_14_2 onward, `PROOF_VERSION_V0` (`0x50524f4f4630`) was added to that list. However, the Cairo OS `check_proof_facts` function hard-asserts `proof_header.proof_version = PROOF_VERSION_V1` — unconditionally rejecting V0. A transaction carrying a V0 proof therefore passes the Rust admission gate but is guaranteed to revert inside the Cairo VM, producing a wrong execution result for an accepted input.

### Finding Description

**Rust pre-validation gate** (`crates/blockifier/src/blockifier/src/transaction/account_transaction.rs`, `validate_proof_facts`):

```rust
if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt()) {
    return Err(...)
}
```

`allowed_proof_versions` is populated directly from the versioned-constants JSON. The V0_14_2 diff adds:

```
+ /os_constants/allowed_proof_versions/0: "0x50524f4f4630"   // PROOF_VERSION_V0
```

So from V0_14_2 onward, a V0 proof passes this check.

**Cairo OS execution gate** (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`, `check_proof_facts`):

```cairo
// Proof version may be V0 (legacy) or V1 (current).
with_attr error_message("Unsupported proof version") {
    assert proof_header.proof_version = PROOF_VERSION_V1;
}
```

The comment acknowledges V0 as a valid legacy version, but the assertion only allows V1. Any V0 proof reaching this point causes an unconditional Cairo assertion failure.

The two constants are defined in `crates/starknet_api/src/transaction/fields.rs`:

```rust
pub const PROOF_VERSION_V0: Felt = Felt::from_hex_unchecked("0x50524f4f4630");
pub const PROOF_VERSION_V1: Felt = Felt::from_hex_unchecked("0x50524f4f4631");
```

The same file's doc-comment states: *"V0 is retained only so that historical blocks carrying V0 proof facts can be replayed (e.g. via reexecution). Whether V0 is accepted is gated per protocol version in the blockifier; the proof verifier no longer supports it."* The `validate_proof_facts` function does not distinguish reexecution from live sequencing, so the gate is equally permissive in both paths.

### Impact Explanation

A transaction with a V0 proof:
1. Passes the Rust `validate_proof_facts` pre-validation (V0 ∈ `allowed_proof_versions` for V0_14_2+).
2. Is admitted to the mempool and included in a block.
3. Hits `check_proof_facts` in the Cairo OS and fails the `assert proof_header.proof_version = PROOF_VERSION_V1` assertion.
4. Produces a revert receipt instead of a success receipt — a wrong execution result for an accepted input.

This matches the allowed impact: **"Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."**

### Likelihood Explanation

Any party that can craft an `InvokeV3` transaction with a V0-formatted `proof_facts` field can trigger this. The gateway stateless validator calls `validate_proof_facts_and_proof_consistency` (a separate code path not examined here), but the blockifier pre-validation path is independently reachable during block building and re-execution. The mismatch is deterministic once V0 is in `allowed_proof_versions`.

### Recommendation

Align the two gates. Either:

1. **Remove `PROOF_VERSION_V0` from `allowed_proof_versions`** in all live (non-reexecution) versioned-constants JSON files, so the Rust gate rejects V0 proofs before they reach the Cairo OS; or
2. **Update `check_proof_facts`** in `execution_constraints.cairo` to accept both V0 and V1 (matching the stale comment), if V0 proofs are genuinely intended to be valid.

The `fields.rs` comment ("the proof verifier no longer supports it") implies option 1 is the intended fix.

### Proof of Concept

1. Construct an `InvokeV3` transaction whose `proof_facts` field begins with `[PROOF_VERSION_V0, VIRTUAL_SNOS, <valid_program_hash>, ...]` (a well-formed V0 SNOS proof header).
2. Submit to a sequencer running protocol version ≥ V0_14_2.
3. **Rust `validate_proof_facts`**: `allowed_proof_versions.contains(PROOF_VERSION_V0)` → `true` → pre-validation passes.
4. **Cairo OS `check_proof_facts`**: `assert proof_header.proof_version = PROOF_VERSION_V1` → `PROOF_VERSION_V0 ≠ PROOF_VERSION_V1` → assertion fails → transaction reverts with "Unsupported proof version".
5. The block includes the transaction with a revert receipt, contradicting the pre-validation acceptance. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L53-56)
```text
    // Proof version may be V0 (legacy) or V1 (current).
    with_attr error_message("Unsupported proof version") {
        assert proof_header.proof_version = PROOF_VERSION_V1;
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L314-320)
```rust
        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L638-647)
```rust
pub const PROOF_VERSION_V0: Felt = Felt::from_hex_unchecked("0x50524f4f4630");

// Represent the `PROOF_VERSION_V1` marker as a Felt ('PROOF1').
pub const PROOF_VERSION_V1: Felt = Felt::from_hex_unchecked("0x50524f4f4631");

/// Supported proof-facts version markers.
///
/// V0 is retained only so that historical blocks carrying V0 proof facts can be replayed (e.g. via
/// reexecution). Whether V0 is accepted is gated per protocol version in the blockifier; the proof
/// verifier no longer supports it.
```

**File:** crates/blockifier/resources/versioned_constants_diff_regression/0.14.1_0.14.2.txt (L6-6)
```text
+ /os_constants/allowed_proof_versions/0: "0x50524f4f4630"
```

**File:** crates/blockifier/src/blockifier_versioned_constants.rs (L121-122)
```rust
    // Proof fact version markers (felts) accepted under this protocol version.
    pub allowed_proof_versions: Vec<Felt>,
```
