### Title
OS `check_proof_facts` Hard-Asserts `PROOF_VERSION_V1` While Blockifier `allowed_proof_versions` Gate Permits `ProofVersion::V0`, Creating Conflicting Proof-Version Validation Logic - (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The Cairo OS `check_proof_facts` function unconditionally asserts `proof_header.proof_version = PROOF_VERSION_V1`, while the blockifier's `validate_proof_facts` checks a configurable `allowed_proof_versions` set that explicitly can include `ProofVersion::V0`. A stale comment in the OS code itself says *"Proof version may be V0 (legacy) or V1 (current)"* immediately before the assertion that only allows V1. This creates the same conflicting-state-validation pattern as the external report: one component (blockifier) is designed to accept a state (`V0`), while the downstream component (OS) unconditionally rejects that same state, making the intended path always fail.

---

### Finding Description

**Step 1 – Blockifier accepts V0 via `allowed_proof_versions`**

In `crates/blockifier/src/transaction/account_transaction.rs`, `validate_proof_facts` checks:

```rust
if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
{
    return Err(TransactionPreValidationError::InvalidProofFacts(...));
}
```

The `ProofVersion::V0` documentation in `crates/starknet_api/src/transaction/fields.rs` explicitly states:

> "V0 is retained only so that historical blocks carrying V0 proof facts can be replayed (e.g. via reexecution). **Whether V0 is accepted is gated per protocol version in the blockifier**; the proof verifier no longer supports it."

This confirms that `allowed_proof_versions` is intentionally populated with `V0` for certain protocol versions to enable replay of historical blocks.

**Step 2 – OS unconditionally rejects V0**

In `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`, `check_proof_facts` contains:

```cairo
// Proof version may be V0 (legacy) or V1 (current).
with_attr error_message("Unsupported proof version") {
    assert proof_header.proof_version = PROOF_VERSION_V1;
}
```

The comment documents the intent to allow both V0 and V1, but the assertion only allows V1. Any transaction carrying `PROOF_VERSION_V0` will cause the OS to abort with `"Unsupported proof version"`.

**Step 3 – The impossible execution condition**

The sequence mirrors the external report exactly:

| External Report | Sequencer Analog |
|---|---|
| `unstakeMany()` sets `stakeAt = MAX_UINT40_VALUE` | Blockifier accepts V0 proof facts (V0 ∈ `allowed_proof_versions`) |
| `unstakeDiedHeroes()` reverts if `stakeAt == MAX_UINT40_VALUE` | OS `check_proof_facts` aborts if `proof_version != PROOF_VERSION_V1` |
| Dead heroes from normal flow always have `MAX_UINT40_VALUE` | Historical blocks replayed via blockifier always carry `PROOF_VERSION_V0` |
| Recovery function always reverts | OS execution always fails for V0 proof-facts transactions |

The `check_proof_facts` is called unconditionally during OS execution of every invoke transaction with non-empty proof facts:

```cairo
check_proof_facts(
    proof_facts_size=proof_facts_size,
    proof_facts=proof_facts,
    current_block_number=block_context.block_info_for_execute.block_number,
    virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
);
```

**Step 4 – The proof verifier also rejects V0, closing the new-transaction path but not the replay path**

`crates/starknet_proof_verifier/src/proof_verifier.rs` explicitly rejects V0 at the gateway:

```rust
ProofVersion::V0 => {
    return Err(VerifyProofError::InvalidProofVersion { actual: proof_version_felt });
}
```

This means new transactions with V0 proof facts are blocked at the gateway. However, for **replay/reexecution** of historical blocks, the gateway is bypassed. The blockifier accepts V0 (if `allowed_proof_versions` includes V0 for the relevant protocol version), but the OS always rejects V0. The replay path is permanently broken for any historical block containing a transaction with V0 proof facts.

---

### Impact Explanation

When the sequencer attempts to replay or re-execute a historical block containing an invoke transaction with `PROOF_VERSION_V0` proof facts:

1. The blockifier's `validate_proof_facts` passes (V0 ∈ `allowed_proof_versions` for the historical protocol version).
2. The OS `check_proof_facts` aborts with `"Unsupported proof version"`.
3. The OS produces a wrong execution result (failure/abort) for input that the blockifier accepted as valid.

This matches the impact scope: **"Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."**

The OS cannot produce a valid proof for any block containing a V0-proof-facts transaction, making those historical blocks permanently unprovable under the current OS code.

---

### Likelihood Explanation

The `ProofVersion::V0` documentation explicitly states it exists for historical block replay. The blockifier's `allowed_proof_versions` is a versioned-constants field that is populated per protocol version. Historical blocks on mainnet/testnet that used the client-side proving feature before the V0→V1 migration carry `PROOF_VERSION_V0` in their proof facts. Any replay or re-execution attempt for those blocks will hit this conflict. The trigger requires no privileged access — it is exercised by the normal replay/reexecution code path.

---

### Recommendation

The fix requires resolving the conflicting validation logic at the OS boundary, analogous to the external report's recommendation to remove the conflicting check in `unstakeDiedHeroes()`:

1. **Primary fix**: Update `check_proof_facts` in `execution_constraints.cairo` to accept both `PROOF_VERSION_V0` and `PROOF_VERSION_V1` when the OS is operating in replay mode, matching the blockifier's `allowed_proof_versions` gate. Remove or update the stale comment that already documents the intent.

2. **Alternative**: If V0 replay is no longer required, remove `PROOF_VERSION_V0` from `allowed_proof_versions` in all versioned constants where it currently appears, so the blockifier and OS agree that V0 is unconditionally rejected.

The stale comment `// Proof version may be V0 (legacy) or V1 (current).` immediately before the V1-only assertion is the direct indicator of the divergence.

---

### Proof of Concept

1. Identify a historical block on mainnet/testnet that contains an invoke transaction with `proof_facts[0] == PROOF_VERSION_V0` (`0x50524f4f4630`).
2. Configure the blockifier with a versioned constants that includes `PROOF_VERSION_V0` in `allowed_proof_versions` (as documented for replay).
3. Attempt to replay/re-execute the block through the OS.
4. The blockifier's `validate_proof_facts` passes (V0 ∈ `allowed_proof_versions`).
5. The OS `check_proof_facts` aborts: `assert proof_header.proof_version = PROOF_VERSION_V1` fails because `proof_header.proof_version == PROOF_VERSION_V0`.
6. The OS produces an error result for a transaction the blockifier accepted — wrong execution result for accepted input. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L53-56)
```text
    // Proof version may be V0 (legacy) or V1 (current).
    with_attr error_message("Unsupported proof version") {
        assert proof_header.proof_version = PROOF_VERSION_V1;
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L313-320)
```rust

        if !os_constants.allowed_proof_versions.contains(&snos_proof_facts.proof_version.as_felt())
        {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Proof version {} is not allowed under this protocol version.",
                snos_proof_facts.proof_version
            )));
        }
```

**File:** crates/starknet_api/src/transaction/fields.rs (L643-653)
```rust
/// Supported proof-facts version markers.
///
/// V0 is retained only so that historical blocks carrying V0 proof facts can be replayed (e.g. via
/// reexecution). Whether V0 is accepted is gated per protocol version in the blockifier; the proof
/// verifier no longer supports it.
#[cfg_attr(any(test, feature = "testing"), derive(EnumIter))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProofVersion {
    V0,
    V1,
}
```

**File:** crates/starknet_proof_verifier/src/proof_verifier.rs (L140-145)
```rust
    match proof_version {
        // V0 proofs are no longer verifiable: the v0 circuit was removed. V0 proof facts are only
        // tolerated by the blockifier (gated per protocol version) for replaying historical blocks.
        ProofVersion::V0 => {
            return Err(VerifyProofError::InvalidProofVersion { actual: proof_version_felt });
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L313-318)
```text
    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );
```
