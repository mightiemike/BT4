## Finding: Missing on-chain/consumer verification that OS-proven `prev_block_hash`/`initial_root` matches the actual previously finalized state

### Title
Starknet OS output does not bind proven state transitions to the actual previous chain state (fake `prev_block_hash`/`initial_root` accepted) - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo`)

### Summary
The Starknet OS Cairo program computes and outputs `prev_block_hash` and `initial_root` for a block's state transition proof, but never actually constrains these values to the real previously-committed chain state — they are simply guessed via a hint and passed through unchecked, exactly mirroring the `Rollup.sol` bug class where `prevStateRoot` was accepted at commit time without verification against the actually-finalized prior root.

### Finding Description
In `get_block_os_output_header`, the OS explicitly documents that it does not verify consistency between the previous block hash/state root and the real chain history: [1](#0-0) 

The `prev_block_hash` value returned by `get_block_hashes` is itself sourced from a hint (`%{ GetBlockHashes %}`) with no cryptographic tie to on-chain history: [2](#0-1) 

The `OsOutputHeader` therefore carries `prev_block_hash`/`initial_root` as free-form, unverified fields: [3](#0-2) 

The only place any linkage is enforced is inside `combine_blocks_inner`, which checks that consecutive blocks *within the same aggregation call* are chained together (`current_header.state_update_output.initial_root == aggregated_header.state_update_output.final_root` and `current_header.prev_block_hash == aggregated_header.new_block_hash`): [4](#0-3) 

This check only validates internal consistency of a multi-block batch being proven together — it does **not** validate that the *first* block's `initial_root`/`prev_block_hash` in the aggregated proof matches the actually-finalized state at that height on the sequencer/chain. Exactly as in the `Rollup.sol` H-2 report, the proof itself can be generated against a fabricated "previous" state, and the binding to the real previous state is deferred entirely to "the consumer of the OS output," per the code's own comment and its `TODO(Yoni)` marker.

### Impact Explanation
If the downstream consumer of this OS/aggregator proof (the component responsible for accepting a state-transition proof as the basis for advancing/finalizing the committed state) does not itself independently re-verify that the proof's `initial_root`/`prev_block_hash` equals the last finalized root/hash, a state transition proven against a fake previous state could be accepted. This can result in: (a) a wrong committed root/block hash being accepted, or (b) a permanent inability to link/finalize the chain of proofs (freezing progress) once the mismatch between the proof's claimed previous state and the actual previous state is discovered — the same "chain freeze" failure mode described in the original report. This satisfies the "wrong committed root or block hash" / "network unable to confirm new transactions" impact bar.

### Likelihood Explanation
The gap is not hypothetical — it is called out explicitly in the source as a known, unaddressed TODO ("verify the consistency of the previous block hash and state root... The consumer of the OS output should verify both"). Any component consuming OS/aggregator output for finalization that trusts these header fields without an explicit external comparison against the last-finalized `global_root`/block hash is exposed. Given that the check is documented as pushed to "the consumer" rather than enforced by the OS/proof itself, this is a systemic soundness gap rather than an isolated operational misconfiguration.

### Recommendation
Enforce, at the point where a state-transition proof/output is consumed to advance finalized state, that `initial_root` (and ideally `prev_block_hash`) of the (aggregated) OS output strictly equals the previously finalized `global_root`/block hash for that height, mirroring how `combine_blocks_inner` already checks internal consistency. Resolve the `TODO(Yoni)` in `os_utils.cairo` by having the OS itself constrain `prev_block_hash`/`initial_root` against a value that is verifiably tied to the actual chain (e.g., via the block-hash-in-storage mechanism already used elsewhere in the OS, similar to `read_block_hash_from_storage`), rather than relying solely on an external, currently-unenforced consumer check.

### Proof of Concept
Not applicable in this static-analysis context — the vulnerable code path is the absence of a check, demonstrated directly by the cited source lines and their accompanying comments/TODOs stating the consistency check is not performed by the OS and is deferred to an unspecified "consumer."

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L120-135)
```text
// Returns the OS output header of the given block.
func get_block_os_output_header{poseidon_ptr: PoseidonBuiltin*}(
    block_context: BlockContext*,
    state_update_output: CommitmentUpdate*,
    os_global_context: OsGlobalContext*,
) -> OsOutputHeader* {
    // Calculate the block hash based on the block info and state root.
    // NOTE: both the previous block hash and previous state root are guessed, and the OS
    // does not verify their consistency (unlike the new hash and root).
    // The consumer of the OS output should verify both.
    // TODO(Yoni): verify the consistency of the previous block hash and state root, and remove the
    // state roots from the OS output header.
    let (prev_block_hash, new_block_hash) = get_block_hashes{poseidon_ptr=poseidon_ptr}(
        block_info=block_context.block_info_for_execute, state_root=state_update_output.final_root
    );

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L55-82)
```text
func get_block_hashes{poseidon_ptr: PoseidonBuiltin*}(block_info: BlockInfo*, state_root: felt) -> (
    previous_block_hash: felt, new_block_hash: felt
) {
    alloc_locals;
    local previous_block_hash;
    // Currently, the header commitments and gas prices are not computed by the OS.
    // TODO(Yoni, 1/1/2027): compute the header commitments and gas prices.
    local header_commitments: BlockHeaderCommitments*;
    local gas_prices_hash;
    // TODO(Yoni): move to global context, and consider enforcing a specific version for the
    // non-virtual OS.
    local starknet_version;

    %{ GetBlockHashes %}

    let block_hash = calculate_block_hash(
        block_info=block_info,
        header_commitments=header_commitments,
        gas_prices_hash=gas_prices_hash,
        state_root=state_root,
        previous_block_hash=previous_block_hash,
        starknet_version=starknet_version,
    );

    %{ CheckBlockHashConsistency %}

    return (previous_block_hash=previous_block_hash, new_block_hash=block_hash);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L33-49)
```text
// The header of the OS output.
struct OsOutputHeader {
    state_update_output: CommitmentUpdate*,
    prev_block_number: felt,
    new_block_number: felt,
    prev_block_hash: felt,
    // Currently, the block hash is not enforced by the OS.
    new_block_hash: felt,
    // The hash of the OS program, if the aggregator was used. Zero if the OS was used directly.
    os_program_hash: felt,
    starknet_os_config_hash: felt,
    // Indicates whether to use KZG commitment scheme instead of adding the data-availability to
    // the transaction data.
    use_kzg_da: felt,
    // Indicates whether previous state values are included in the state update information.
    full_output: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/aggregator/combine_blocks.cairo (L151-162)
```text
    // Validate fields of the inner OS output of a single task.
    assert current_header.use_kzg_da = 0;
    assert current_header.full_output = 1;
    assert current_header.os_program_hash = 0;

    // Check header consistency.
    assert current_header.state_update_output.initial_root = (
        aggregated_header.state_update_output.final_root
    );
    assert current_header.prev_block_number = aggregated_header.new_block_number;
    assert current_header.prev_block_hash = aggregated_header.new_block_hash;
    assert current_header.starknet_os_config_hash = aggregated_header.starknet_os_config_hash;
```
