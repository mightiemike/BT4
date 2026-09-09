import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 30
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'paradigmxyz/reth'
# todo: the name of the repository
REPO_NAME = 'reth'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    # =================================================================================
    # LENS: BLOCK VALIDITY, STATE ROOT AND POOL ADMISSION (reth execution client).
    # Reth receives a block from an honest consensus layer through engine_newPayload /
    # engine_forkchoiceUpdated, receives transactions from any user through the pool,
    # executes them with revm, computes the state root, and answers VALID / INVALID.
    # It also builds blocks for local proposers out of the pool. The files below sit on
    # the path from those inputs to one of four decisions: is the block reth accepts the
    # block the Ethereum spec accepts, does the state root and receipts root reth computes
    # equal the roots a full recompute gives, does the state a transaction reads equal the
    # parent's post-state, and does the block reth builds pass reth's own validation.
    # A question belongs here only if it can be closed by an equality between what the
    # spec (or a full recompute) says and what reth says for the same block or tx.
    # =================================================================================

    # -- consensus: header, body, pre- and post-execution block rules --------------------
    "crates/consensus/common/src/lib.rs",
    "crates/consensus/common/src/validation.rs",
    "crates/consensus/consensus/src/lib.rs",
    "crates/ethereum/consensus/src/lib.rs",
    "crates/ethereum/consensus/src/validation.rs",

    # -- payload well-formedness: engine payload -> sealed block --------------------------
    "crates/payload/validator/src/cancun.rs",
    "crates/payload/validator/src/lib.rs",
    "crates/payload/validator/src/prague.rs",
    "crates/payload/validator/src/shanghai.rs",
    "crates/ethereum/payload/src/config.rs",
    "crates/ethereum/payload/src/lib.rs",
    "crates/ethereum/payload/src/validator.rs",
    "crates/ethereum/engine-primitives/src/error.rs",
    "crates/ethereum/engine-primitives/src/lib.rs",
    "crates/ethereum/engine-primitives/src/payload.rs",

    # -- engine API surface and engine primitives ------------------------------------------
    "crates/rpc/rpc-engine-api/src/capabilities.rs",
    "crates/rpc/rpc-engine-api/src/engine_api.rs",
    "crates/rpc/rpc-engine-api/src/error.rs",
    "crates/rpc/rpc-engine-api/src/lib.rs",
    "crates/rpc/rpc-engine-api/src/metrics.rs",
    "crates/rpc/rpc-engine-api/src/reth_engine_api.rs",
    "crates/engine/primitives/src/config.rs",
    "crates/engine/primitives/src/error.rs",
    "crates/engine/primitives/src/event.rs",
    "crates/engine/primitives/src/forkchoice.rs",
    "crates/engine/primitives/src/invalid_block_hook.rs",
    "crates/engine/primitives/src/lib.rs",
    "crates/engine/primitives/src/message.rs",

    # -- engine tree: newPayload / forkchoice handling, execution, state root, persistence --
    "crates/engine/tree/src/backfill.rs",
    "crates/engine/tree/src/chain.rs",
    "crates/engine/tree/src/download.rs",
    "crates/engine/tree/src/engine.rs",
    "crates/engine/tree/src/launch.rs",
    "crates/engine/tree/src/lib.rs",
    "crates/engine/tree/src/metrics.rs",
    "crates/engine/tree/src/persistence.rs",
    "crates/engine/tree/src/tree/block_buffer.rs",
    "crates/engine/tree/src/tree/error.rs",
    "crates/engine/tree/src/tree/instrumented_state.rs",
    "crates/engine/tree/src/tree/invalid_headers.rs",
    "crates/engine/tree/src/tree/metrics.rs",
    "crates/engine/tree/src/tree/mod.rs",
    "crates/engine/tree/src/tree/payload_processor/bal_prewarm_pool.rs",
    "crates/engine/tree/src/tree/payload_processor/bal/error.rs",
    "crates/engine/tree/src/tree/payload_processor/bal/execute.rs",
    "crates/engine/tree/src/tree/payload_processor/bal/mod.rs",
    "crates/engine/tree/src/tree/payload_processor/bal/ordered_outputs.rs",
    "crates/engine/tree/src/tree/payload_processor/bal/worker.rs",
    "crates/engine/tree/src/tree/payload_processor/mod.rs",
    "crates/engine/tree/src/tree/payload_processor/prewarm.rs",
    "crates/engine/tree/src/tree/payload_processor/receipt_root_task.rs",
    "crates/engine/tree/src/tree/payload_validator.rs",
    "crates/engine/tree/src/tree/persistence_state.rs",
    "crates/engine/tree/src/tree/precompile_cache.rs",
    "crates/engine/tree/src/tree/state_root_strategy/mod.rs",
    "crates/engine/tree/src/tree/state_root_strategy/sparse_trie.rs",
    "crates/engine/tree/src/tree/state.rs",
    "crates/engine/tree/src/tree/trie_updates.rs",
    "crates/engine/tree/src/tree/txpool_prewarm/control.rs",
    "crates/engine/tree/src/tree/txpool_prewarm/mod.rs",
    "crates/engine/tree/src/tree/txpool_prewarm/worker.rs",
    "crates/engine/tree/src/tree/types.rs",
    "crates/engine/execution-cache/src/cached_state.rs",
    "crates/engine/execution-cache/src/lib.rs",
    "crates/engine/execution-cache/src/txpool.rs",

    # -- execution: evm config, block assembly, receipts, executor, sender recovery --------
    "crates/ethereum/evm/src/build.rs",
    "crates/ethereum/evm/src/config.rs",
    "crates/ethereum/evm/src/factory.rs",
    "crates/ethereum/evm/src/lib.rs",
    "crates/ethereum/evm/src/receipt.rs",
    "crates/ethereum/primitives/src/lib.rs",
    "crates/ethereum/primitives/src/receipt.rs",
    "crates/evm/evm/src/aliases.rs",
    "crates/evm/evm/src/either.rs",
    "crates/evm/evm/src/engine.rs",
    "crates/evm/evm/src/execute.rs",
    "crates/evm/evm/src/lib.rs",
    "crates/evm/evm/src/metrics.rs",
    "crates/evm/evm/src/sender_recovery.rs",
    "crates/evm/execution-errors/src/lib.rs",
    "crates/evm/execution-errors/src/trie.rs",
    "crates/evm/execution-types/src/chain.rs",
    "crates/evm/execution-types/src/execute.rs",
    "crates/evm/execution-types/src/execution_outcome.rs",
    "crates/evm/execution-types/src/lib.rs",
    "crates/revm/src/cached.rs",
    "crates/revm/src/cancelled.rs",
    "crates/revm/src/database.rs",
    "crates/revm/src/lib.rs",
    "crates/revm/src/witness.rs",

    # -- hardforks and chain spec: which rules apply at which block / timestamp -----------
    "crates/chainspec/src/api.rs",
    "crates/chainspec/src/constants.rs",
    "crates/chainspec/src/info.rs",
    "crates/chainspec/src/lib.rs",
    "crates/chainspec/src/spec.rs",
    "crates/ethereum/hardforks/src/display.rs",
    "crates/ethereum/hardforks/src/hardforks/dev.rs",
    "crates/ethereum/hardforks/src/hardforks/mod.rs",
    "crates/ethereum/hardforks/src/lib.rs",

    # -- payload building: pool -> block for a local proposer ----------------------------
    "crates/payload/basic/src/better_payload_emitter.rs",
    "crates/payload/basic/src/lib.rs",
    "crates/payload/basic/src/metrics.rs",
    "crates/payload/basic/src/stack.rs",
    "crates/payload/builder/src/lib.rs",
    "crates/payload/builder/src/metrics.rs",
    "crates/payload/builder/src/service.rs",
    "crates/payload/builder/src/traits.rs",
    "crates/payload/primitives/src/error.rs",
    "crates/payload/primitives/src/lib.rs",
    "crates/payload/primitives/src/payload.rs",
    "crates/payload/primitives/src/traits.rs",
    "crates/payload/util/src/lib.rs",
    "crates/payload/util/src/traits.rs",
    "crates/payload/util/src/transaction.rs",

    # -- transaction pool: admission, blob sidecars, ordering, head updates ---------------
    "crates/transaction-pool/src/batcher.rs",
    "crates/transaction-pool/src/blobstore/converter.rs",
    "crates/transaction-pool/src/blobstore/disk.rs",
    "crates/transaction-pool/src/blobstore/mem.rs",
    "crates/transaction-pool/src/blobstore/mod.rs",
    "crates/transaction-pool/src/blobstore/tracker.rs",
    "crates/transaction-pool/src/config.rs",
    "crates/transaction-pool/src/error.rs",
    "crates/transaction-pool/src/identifier.rs",
    "crates/transaction-pool/src/lib.rs",
    "crates/transaction-pool/src/maintain.rs",
    "crates/transaction-pool/src/metrics.rs",
    "crates/transaction-pool/src/ordering.rs",
    "crates/transaction-pool/src/pool/best.rs",
    "crates/transaction-pool/src/pool/blob.rs",
    "crates/transaction-pool/src/pool/events.rs",
    "crates/transaction-pool/src/pool/listener.rs",
    "crates/transaction-pool/src/pool/mod.rs",
    "crates/transaction-pool/src/pool/parked.rs",
    "crates/transaction-pool/src/pool/pending.rs",
    "crates/transaction-pool/src/pool/size.rs",
    "crates/transaction-pool/src/pool/state.rs",
    "crates/transaction-pool/src/pool/txpool.rs",
    "crates/transaction-pool/src/pool/update.rs",
    "crates/transaction-pool/src/traits.rs",
    "crates/transaction-pool/src/validate/constants.rs",
    "crates/transaction-pool/src/validate/eth.rs",
    "crates/transaction-pool/src/validate/mod.rs",
    "crates/transaction-pool/src/validate/task.rs",

    # -- trie: hashed state, sparse trie, parallel proofs, state root ---------------------
    "crates/trie/common/src/account.rs",
    "crates/trie/common/src/constants.rs",
    "crates/trie/common/src/execution_witness.rs",
    "crates/trie/common/src/hash_builder/mod.rs",
    "crates/trie/common/src/hash_builder/state.rs",
    "crates/trie/common/src/hashed_state.rs",
    "crates/trie/common/src/input.rs",
    "crates/trie/common/src/key.rs",
    "crates/trie/common/src/lib.rs",
    "crates/trie/common/src/nibbles.rs",
    "crates/trie/common/src/ordered_root.rs",
    "crates/trie/common/src/prefix_set.rs",
    "crates/trie/common/src/proofs.rs",
    "crates/trie/common/src/range_proof.rs",
    "crates/trie/common/src/root.rs",
    "crates/trie/common/src/storage.rs",
    "crates/trie/common/src/subnode.rs",
    "crates/trie/common/src/target_v2.rs",
    "crates/trie/common/src/trie_data.rs",
    "crates/trie/common/src/trie_node_v2.rs",
    "crates/trie/common/src/trie.rs",
    "crates/trie/common/src/updates.rs",
    "crates/trie/common/src/utils.rs",
    "crates/trie/db/src/changesets.rs",
    "crates/trie/db/src/hashed_cursor.rs",
    "crates/trie/db/src/lib.rs",
    "crates/trie/db/src/prefix_set.rs",
    "crates/trie/db/src/proof.rs",
    "crates/trie/db/src/state.rs",
    "crates/trie/db/src/storage.rs",
    "crates/trie/db/src/trie_cursor.rs",
    "crates/trie/parallel/src/error.rs",
    "crates/trie/parallel/src/lib.rs",
    "crates/trie/parallel/src/proof_task_metrics.rs",
    "crates/trie/parallel/src/proof_task.rs",
    "crates/trie/parallel/src/state_root_task.rs",
    "crates/trie/parallel/src/value_encoder.rs",
    "crates/trie/sparse/src/arena/branch_child_idx.rs",
    "crates/trie/sparse/src/arena/cursor.rs",
    "crates/trie/sparse/src/arena/mod.rs",
    "crates/trie/sparse/src/arena/nodes.rs",
    "crates/trie/sparse/src/lib.rs",
    "crates/trie/sparse/src/metrics.rs",
    "crates/trie/sparse/src/state.rs",
    "crates/trie/sparse/src/traits.rs",
    "crates/trie/sparse/src/trie.rs",
    "crates/trie/trie/src/changesets.rs",
    "crates/trie/trie/src/forward_cursor.rs",
    "crates/trie/trie/src/hashed_cursor/metrics.rs",
    "crates/trie/trie/src/hashed_cursor/mod.rs",
    "crates/trie/trie/src/hashed_cursor/post_state.rs",
    "crates/trie/trie/src/lib.rs",
    "crates/trie/trie/src/metrics.rs",
    "crates/trie/trie/src/node_iter.rs",
    "crates/trie/trie/src/progress.rs",
    "crates/trie/trie/src/proof_v2/mod.rs",
    "crates/trie/trie/src/proof_v2/node.rs",
    "crates/trie/trie/src/proof_v2/target.rs",
    "crates/trie/trie/src/proof_v2/value.rs",
    "crates/trie/trie/src/proof/mod.rs",
    "crates/trie/trie/src/stats.rs",
    "crates/trie/trie/src/trie_cursor/depth_first.rs",
    "crates/trie/trie/src/trie_cursor/in_memory.rs",
    "crates/trie/trie/src/trie_cursor/metrics.rs",
    "crates/trie/trie/src/trie_cursor/mod.rs",
    "crates/trie/trie/src/trie_cursor/subnode.rs",
    "crates/trie/trie/src/trie.rs",
    "crates/trie/trie/src/verify.rs",
    "crates/trie/trie/src/walker.rs",
    "crates/trie/trie/src/witness.rs",

    # -- state reads: in-memory chain, overlays, providers, persistence writer -------------
    "crates/chain-state/src/chain_info.rs",
    "crates/chain-state/src/execution_stats.rs",
    "crates/chain-state/src/in_memory.rs",
    "crates/chain-state/src/lib.rs",
    "crates/chain-state/src/memory_overlay.rs",
    "crates/chain-state/src/notifications.rs",
    "crates/chain-state/src/preserved_sparse_trie.rs",
    "crates/storage/storage-overlay/src/builder.rs",
    "crates/storage/storage-overlay/src/changeset_cache.rs",
    "crates/storage/storage-overlay/src/lib.rs",
    "crates/storage/storage-overlay/src/manager_metrics.rs",
    "crates/storage/storage-overlay/src/manager.rs",
    "crates/storage/storage-overlay/src/provider.rs",
    "crates/storage/provider/src/bal.rs",
    "crates/storage/provider/src/bal/rocksdb.rs",
    "crates/storage/provider/src/changeset_walker.rs",
    "crates/storage/provider/src/changesets_utils/mod.rs",
    "crates/storage/provider/src/changesets_utils/state_reverts.rs",
    "crates/storage/provider/src/either_writer.rs",
    "crates/storage/provider/src/init.rs",
    "crates/storage/provider/src/lib.rs",
    "crates/storage/provider/src/providers/blockchain_provider.rs",
    "crates/storage/provider/src/providers/consistent.rs",
    "crates/storage/provider/src/providers/database/builder.rs",
    "crates/storage/provider/src/providers/database/chain.rs",
    "crates/storage/provider/src/providers/database/metrics.rs",
    "crates/storage/provider/src/providers/database/mod.rs",
    "crates/storage/provider/src/providers/database/provider.rs",
    "crates/storage/provider/src/providers/database/save_blocks.rs",
    "crates/storage/provider/src/providers/mod.rs",
    "crates/storage/provider/src/providers/rocksdb/invariants.rs",
    "crates/storage/provider/src/providers/rocksdb/metrics.rs",
    "crates/storage/provider/src/providers/rocksdb/mod.rs",
    "crates/storage/provider/src/providers/rocksdb/provider.rs",
    "crates/storage/provider/src/providers/state/historical.rs",
    "crates/storage/provider/src/providers/state/latest.rs",
    "crates/storage/provider/src/providers/state/mod.rs",
    "crates/storage/provider/src/providers/static_file/jar.rs",
    "crates/storage/provider/src/providers/static_file/manager.rs",
    "crates/storage/provider/src/providers/static_file/metrics.rs",
    "crates/storage/provider/src/providers/static_file/mod.rs",
    "crates/storage/provider/src/providers/static_file/writer.rs",
    "crates/storage/provider/src/traits/full.rs",
    "crates/storage/provider/src/traits/mod.rs",
    "crates/storage/provider/src/traits/rocksdb_provider.rs",
    "crates/storage/provider/src/traits/static_file_provider.rs",
    "crates/storage/provider/src/writer/mod.rs",

    # =================================================================================
    # NOT AUDITED (excluded from every variant): tests.rs, tests/ and benches/ directories,
    # test_utils, test_data, mock and noop implementations, writer_tests.rs; the vendored
    # libmdbx sources; networking crates (crates/net/**) and anything that needs a malicious
    # peer; the public JSON-RPC namespaces (crates/rpc/rpc/**); the debug engine stream
    # helpers in crates/engine/util; CLI, node builder, exex, era, etl, prune, stages and
    # static-file crates; Cargo.toml, Makefile, docs, generated CLI docs, README, CLAUDE.md.
    # A defect in any of these is only in scope when it is reachable from the audited code
    # above.
    # =================================================================================
]


target_scopes = [
    "Critical. THE BLOCK RETH ACCEPTS MUST BE THE BLOCK THE SPEC ACCEPTS. `ensure_well_formed_payload` seals the payload, compares `block_hash`, then runs `shanghai::ensure_well_formed_fields`, `cancun::ensure_well_formed_fields` (`ensure_well_formed_header_and_sidecar_fields`, `ensure_matching_blob_versioned_hashes` zipping sidecar `versioned_hashes` against `blob_versioned_hashes_iter`) and `prague::ensure_well_formed_fields`; `EthBeaconConsensus::validate_header` and `validate_header_against_parent` run `validate_header_gas`, `validate_header_base_fee`, `validate_header_extra_data`, `validate_against_parent_hash_number`, `validate_against_parent_eip1559_base_fee`, `validate_against_parent_timestamp`, `validate_against_parent_gas_limit`, `validate_against_parent_4844`, `validate_4844_header_standalone`; `validate_block_pre_execution_with_tx_root` runs `post_merge_hardfork_fields` (ommers, `validate_shanghai_withdrawals`, `validate_cancun_gas`, `MAX_RLP_BLOCK_SIZE`) and a caller-supplied `transaction_root`; `validate_block_with_state` only awaits `spawn_convert_and_validate` early when gas_limit exceeds `MAX_EXPECTED_GAS_LIMIT_MULTIPLIER`. Probe every header and body field a permissionless proposer controls: `blob_gas_used` / `excess_blob_gas` / `parent_beacon_block_root` / `requests_hash` / `block_access_list_hash` presence versus the fork at `timestamp`; a withdrawals list or `extra_data` at the boundary; blob hashes reordered between body and sidecar; an `Option` that defaults to a passing value. Identity: the set of blocks reth answers VALID for == the set the Ethereum spec accepts, for the same parent.",

    "Critical. THE ROOTS RETH CHECKS MUST BE THE ROOTS A FULL RECOMPUTE GIVES. `validate_block_post_execution_with_bal_hashes` compares `gas_used`, then either `compare_receipts_root_and_logs_bloom` on the `(receipts_root, logs_bloom)` streamed from `ReceiptRootTaskHandle` (fed per tx through `IndexedReceipt` by `execute_transactions`) or `verify_receipts` on `result.receipts`; then `requests_hash`; then the BAL hash under `allow_bal_hashes`. `validate_post_execution` in payload_validator then compares the header `state_root` with what `StateRootJob::finish` returns from the sparse-trie, parallel or serial strategy, and `PreservedSparseTrie` / `take_sparse_trie` reuses a trie across blocks. Show a block where the pre-computed side differs from the full recompute yet reth accepts it, or where the full recompute would pass and reth rejects: a receipt indexed to the wrong position when a tx errors mid-block; a `receipt_root_bloom` derived from fewer receipts than `transaction_count`; a `requests` list ordered differently from the header; a state root taken from a preserved trie anchored at another parent; a BAL rebuilt from worker outputs that diverges from canonical execution but is only logged. Identity: (state_root, receipts_root, logs_bloom, requests_hash, gas_used, block_access_list_hash) reth validated == the same six values recomputed from scratch over the block's executed state.",

    "Critical. EXECUTION MUST BE DETERMINISTIC AND EQUAL THE SPEC FOR ANY BYTECODE. `EthEvmConfig` builds the `EvmEnv` and `EthBlockExecutionCtx`; `execute_transactions` streams txs from `PayloadHandle::iter_transactions`; `CachedPrecompile::call` returns a cached `CacheEntry` keyed on `(input.data, spec_id)` whenever `input.gas >= entry.gas_used`, and only inserts when `reservoir` and `state_gas_used` are untouched; `SenderRecoveryCache::recover` caches sender by tx hash; `JitPauseGuard` and `with_jit_support` toggle JIT; the BAL path in `bal::execute_block` runs workers speculatively over `make_db(true)`, commits in order through `ordered_worker_outputs`, and `GasTracker::validate_tx_limit` admits gas. Probe what an unprivileged deployer can put in a transaction's calldata or contract code: a precompile whose output depends on gas or address yet is served from cache; the same calldata under two spec ids; a tx whose sender recovery differs between pool and block; a worker result committed on a stale parent read; a state-gas budget that admits a tx the serial path rejects. Identity: the `BlockExecutionOutput` (receipts, gas_used, bundle state, BAL) from the cached, JIT, prewarmed or parallel path == the output of plain serial revm execution of the same block, and == the spec.",

    "Critical. THE STATE A TRANSACTION READS MUST BE THE PARENT'S POST-STATE. `overlay_state_provider_factory` builds a provider from `OverlayManager::overlay_builder(parent_hash)`, `MemoryOverlayStateProvider` layers `ExecutedBlock`s over a historical provider, `CachedStateProvider::new_with_mode` with `CacheFillMode` serves `ExecutionCache` entries saved by `PayloadProcessor::on_inserted_executed_block` / `cache_for(parent_hash)`, `TxPoolPrewarmCacheSnapshot` answers `account` / `storage` / `bytecode` for a `parent_hash`, `ChangesetCache::get_or_compute_range` aggregates reverts, and `CanonicalInMemoryState::update_chain` / `remove_persisted_blocks_until` move blocks to disk while `HistoricalStateProviderRef` and `LatestStateProviderRef` serve `basic_account`, `storage`, `bytecode_by_hash` and `block_hash`. Show an unprivileged tx or block sequence (sibling blocks at the same height, a reorg across the persistence boundary, a self-destructed and recreated contract, a BLOCKHASH lookup near the tip) where a read served from a cache, snapshot or overlay differs from the value in the parent's committed state, so two reth nodes with different cache histories execute the same block differently. Identity: every (account, storage slot, code, block hash) value the EVM reads while executing block B == the same value in the post-state of B's parent as stored on disk.",

    "Critical. THE STATE ROOT MUST EQUAL THE ROOT OF THE HASHED POST-STATE. `evm_state_to_hashed_post_state` turns the EVM state into a `HashedPostState`; `SparseStateTrie::update_leaves` / `reveal_decoded_multiproof_v2` / `root_with_updates` / `prune` and `SparseTrie::root(new_epoch)` keep a partially revealed trie; `ParallelProof` and `proof_task` fetch nodes; `PrefixSetMut` and `TriePrefixSets` decide which paths are revisited; `TrieUpdates` produced by one block feed `compute_block_trie_updates` and `take_trie_updates` for the next; `HashedPostStateSorted` and `StorageTrieUpdates` carry `wiped` flags. Show a tx pattern an unprivileged user can deploy (SELFDESTRUCT then CREATE2 at the same address in one block, storage cleared to zero then re-set, an account emptied to EIP-161 state, thousands of slots under one account) where the incremental or sparse root differs from the root computed by `StateRoot::from_tx` over the full hashed state. Identity: `root(sparse or parallel, incremental)` == `root(full recompute)` for every block, and the `TrieUpdates` persisted after block N reproduce the trie a fresh node builds after block N.",

    "High. POOL ADMISSION MUST EQUAL BLOCK VALIDITY FOR THE NEXT BLOCK. `EthTransactionValidator::validate_stateless` checks type gating, `Eip2681`, `max_tx_input_bytes`, `ensure_max_init_code_size`, `max_gas_limit`, `TipAboveFeeCap`, `ChainIdMismatch`, `ensure_intrinsic_gas`, blob count against `ForkTracker::max_blob_count`, and `tx_gas_limit_cap`; `validate_stateful` checks `validate_sender_bytecode` (only EIP-7702 delegations), `validate_sender_nonce`, `validate_sender_balance` via `cost()`, and `validate_eip4844` (`EthBlobTransactionSidecar::Missing` trusts `blob_store.contains`, 4844 vs 7594 sidecar gating on `is_osaka_activated`); `on_new_head_block` flips `ForkTracker` atomics; `TxPool::set_block_info` and `update` move txs between pending and parked; `maintain_transaction_pool` reinserts on reorg; `BestTransactions` orders by `ordering`. Show a tx an ordinary user can broadcast that is accepted here but invalid in the block reth builds from it, or valid on chain but rejected or evicted here: a 7702 tx whose `authorization_list` recovers no authority, a blob tx with a sidecar version the next fork forbids, a nonce gap closed by a reorg, a fee-cap edge at a fork timestamp. Identity: for the head reth is building on, pool_accepts(tx) == block_valid(tx) under `validate_block_with_state`.",

    "High. THE BLOCK RETH BUILDS MUST PASS RETH'S OWN VALIDATION. `default_ethereum_payload` pulls `best_transactions` from the pool, skips on `InvalidTransaction` / `ValidationError`, tracks `cumulative_gas_used`, blob gas against `max_blob_gas_per_block`, withdrawals, and applies `BlockExecutor::finish`; `EthBlockAssembler::assemble_block` fills `state_root`, `receipts_root`, `logs_bloom`, `blob_gas_used`, `excess_blob_gas`, `requests_hash` and `block_access_list_hash`; `BasicPayloadJob` and `BetterPayloadEmitter` race builds; `EthBuiltPayload::try_into_v3..v6` and `into_execution_data` shape what the CL gets back. Show an unprivileged tx that, once selected by the builder, yields a block that `validate_block_with_state` or another client rejects: a tx crossing the EIP-7825 gas cap or `MAX_RLP_BLOCK_SIZE`, a blob tx pushing `blob_gas_used` above the per-block max, an authorization list that changes sender nonce mid-block, a receipt whose cumulative gas disagrees with the header. Identity: `EthBeaconConsensus::validate_block_post_execution` and `ensure_well_formed_payload` applied to a payload from `default_ethereum_payload` == Ok, for every pool contents an unprivileged user can produce.",

    "Critical. THE FORK RULES APPLIED MUST BE THE FORK RULES AT THAT BLOCK, EVERYWHERE. `ChainSpec::base_fee_params_at_timestamp`, `blob_params_to_schedule`, `is_*_active_at_timestamp` versus `is_*_active_at_block`, `EthereumHardfork` ordering and `ForkCondition` for `ChainSpecBuilder::mainnet`; `EthEvmConfig` picks `SpecId` from the header timestamp; `ForkTracker` in the pool is updated only on `on_new_head_block`; `cancun::ensure_well_formed_fields` and `prague::ensure_well_formed_fields` take `is_*_active` booleans computed once from `sealed_block.timestamp`; `validate_against_parent_4844` uses the parent's blob params; `EthBeaconConsensus` and payload validation both consult `chain_spec`. Show a block or tx at a fork boundary (first block after a BPO blob schedule change, a parent before and child after Osaka or Amsterdam, a genesis-timestamp fork) where two code paths in reth pick different rule sets or reth picks a different set than the spec: a blob count checked against the parent's fork, a base fee computed with the child's params, a `SpecId` older than the header's fork. Identity: for every block, the (SpecId, base fee params, blob params, active EIP set) used by execution == used by header validation == used by the pool == the spec's activation schedule.",

    "High. FORKCHOICE MUST TRACK ONLY BLOCKS RETH ITSELF PROVED. `on_new_payload` -> `try_insert_payload` / `try_buffer_payload` -> `insert_block_or_payload`; `InvalidHeaderCache::insert_with_invalid_ancestor` and `check_invalid_ancestor_with_head` propagate invalidity to descendants; `latest_valid_hash_for_invalid_payload` and `prepare_invalid_response` answer the CL; `on_forkchoice_updated` -> `validate_forkchoice_state`, `handle_canonical_head`, `apply_chain_update`, `update_finalized_block` / `update_safe_block`; `BlockBuffer::insert_block` / `remove_block_with_children`; `find_disk_reorg`, `remove_blocks` and `on_persistence_complete` reconcile memory and disk; `EngineApiTreeState::insert_executed` / `remove_until`. Show a permissionless proposer's block sequence (an invalid block followed by a valid sibling, a block invalid only under `Other` errors, a chain reorged across the persisted watermark) where reth marks a valid block INVALID, returns a `latest_valid_hash` that is not the last valid ancestor, or canonicalizes a block it never executed, so all reth nodes leave the canonical chain without any malicious peer. Identity: the set of hashes reth reports VALID / canonical == the set of blocks it executed and validated on the current canonical parent chain.",

    "Critical. THE MISSING INVARIANT - what nobody built. No check ties the `(receipts_root, logs_bloom)` streamed by `ReceiptRootTaskHandle` back to `result.receipts` when both exist; nothing asserts a `PreservedSparseTrie` or `ExecutionCache` hit was produced on the same parent the block declares beyond a hash comparison at save time; `bal::execute_block` only logs BAL divergence between worker and canonical execution; `bal_path_eligible` gates on BAL presence rather than the Amsterdam fork; `validate_eip4844` accepts `Missing` sidecars on `blob_store.contains`; `CachedPrecompile` assumes every cacheable precompile is pure; `validate_block_with_state` skips awaiting pre-execution checks unless gas_limit jumps. Identify the FIRST place one of these unstated equalities is violated by an unprivileged user with a transaction, contract bytecode, or a permissionlessly proposed block delivered by an honest CL, prove it with a Rust test that asserts both sides (reth's verdict versus the spec's, cached root versus recomputed root, cached read versus committed state, built block versus own validation) before and after, and show that no later step in `insert_block_or_payload` can detect or reverse it.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate block-validity / state-root / pool-admission audit questions for one reth target.

    ```
    target_file format:
    "'File Name: crates/engine/tree/src/tree/payload_validator.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate execution-client security audit questions for this exact reth target:

    {target_file}

    Project focus:
    Reth is an Ethereum execution client. An honest consensus layer hands it blocks
    through engine_newPayload / engine_forkchoiceUpdated; any user hands it transactions
    through the pool; contract bytecode runs inside revm; it computes state and receipts
    roots and answers VALID or INVALID; it builds blocks from the pool for local
    proposers. Untrusted input is whatever a permissionless proposer puts in a block,
    whatever an ordinary user puts in a transaction or deploys as code, and any state
    those leave behind. The system decides (a) whether the block reth accepts equals the
    block the spec accepts; (b) whether the roots reth checks equal a full recompute;
    (c) whether the state a transaction reads equals the parent's post-state and
    execution is deterministic across cached, prewarmed, JIT and parallel paths; (d)
    whether the block reth builds passes its own validation and pool admission equals
    block validity. Any block, root, read or verdict that differs from the spec is the
    bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (function, method, struct, enum variant, const, error
      variant) as they appear in the file.
    * EVERY question must close on an equality that must hold across a call. State it
      explicitly. Narrative questions with no stated equality are rejected.
    * Attacker is unprivileged only: an ordinary Ethereum user with their own funds and
      keys who can broadcast any transaction, deploy any bytecode, and order their own
      transactions; or a permissionless block proposer / builder whose block an HONEST
      consensus layer delivers to reth through the Engine API. They control every byte
      of a transaction and every header and body field of their own block.
    * Attacker is NOT a malicious peer, node, RPC client, consensus layer, or node
      operator; no compromised dependency, no misconfiguration flags
      (`--disable-balance-check`, `with_skip_*`, `with_allow_bal_hashes`), no leaked keys,
      no social engineering.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - Tests, benches, test_utils, mocks, noop impls, vendored libmdbx, networking
        crates, public JSON-RPC namespaces, CLI, docs and Cargo files are OUT OF SCOPE.
      - Denial of service, resource exhaustion, unbounded memory or cache growth, rate
        limiting, timeouts, slow paths and single-node crashes needing sustained load
        are OUT OF SCOPE.
      - Defects inside revm, alloy or c-kzg with no path through this repo are OUT OF
        SCOPE; reth misusing them (wrong env, wrong spec, wrong cache key) is IN scope.
      - Also excluded: centralization risk, best-practice notes, feature requests,
        publicly known issues, findings only reproducible through tests or tooling.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: consensus split - reth accepts a block the spec rejects or rejects a
      block the spec accepts; a state root, receipts root or balance that differs from
      the spec (state corruption, infinite ETH); a deterministic panic or wrong verdict
      on every reth node from one transaction or block.
      High: reth-built blocks rejected by other clients; a valid canonical chain marked
      invalid or a wrong latest_valid_hash that stalls reth nodes until manual
      intervention; pool admission diverging from block validity so valid transactions
      are censored or invalid ones are built; non-deterministic execution between the
      cached, prewarmed, JIT or parallel path and serial execution.
    * Every question must be a concrete real-world scenario an unprivileged party can
      trigger with a transaction, bytecode, or a permissionlessly proposed block.
    * A returned error or panic is a finding only when it makes reth's verdict differ
      from the spec or stops every reth node on a valid chain - say which.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable locally with a Rust test (`cargo nextest`) using
      an in-memory or dev-chain provider. Never propose testing on mainnet or a public
      testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are:
      reth verdict and spec verdict, cached root and recomputed root, cached read and
      committed state, parallel output and serial output, built block and own
      validation, pool admission and block validity, fork rules used and fork rules due.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a malicious peer, CL, RPC caller, operator or flag.
    * A bug in revm, alloy or c-kzg with no path here.
    * DoS, memory, timing, logging, metrics, or a user harming only their own funds.
    * Findings only reproducible through tests or tooling.

    Core equalities (each question must close on one):
    * VERDICT TRUTH: reth VALID / INVALID for block B == spec VALID / INVALID for B.
    * ROOT TRUTH: (state_root, receipts_root, logs_bloom, requests_hash, BAL hash) checked
      == same values from a full recompute.
    * READ TRUTH: every account, slot, code and block hash the EVM reads == parent's
      committed post-state.
    * PATH DETERMINISM: cached / prewarmed / JIT / parallel execution output == serial.
    * BUILD TRUTH: the block reth builds passes reth's own validation and other clients'.
    * ADMISSION TRUTH: pool_accepts(tx) == block_valid(tx) on the current head.
    * FORK TRUTH: rule set used by execution == validation == pool == spec schedule.

    Each question must include:
    1. target function, method, struct or const;
    2. attacker input (the concrete header field, body field, tx field, calldata or
       bytecode pattern that matters);
    3. preconditions (fork, parent state, cache or overlay state, chain shape);
    4. call sequence through the engine tree, executor, trie or pool;
    5. the equality that breaks, written explicitly;
    6. scoped impact and how many nodes it hits;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: function_name] Can an unprivileged ATTACKER_INPUT under PRECONDITIONS trigger CALL_SEQUENCE, breaking the equality EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: cargo nextest test PARAMETERS asserting VERDICT_TRUTH, ROOT_TRUTH, READ_TRUTH, PATH_DETERMINISM, BUILD_TRUTH, ADMISSION_TRUTH, or FORK_TRUTH.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a block-validity / state-root exploit-validation prompt for reth.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an ordinary Ethereum user who can broadcast any transaction and deploy any bytecode, or a permissionless block proposer / builder whose block an HONEST consensus layer delivers through the Engine API. They control every byte of their transaction and every field of their own block.
- Reject anything requiring a malicious peer, node, RPC caller, consensus layer or operator, a misconfiguration flag, a compromised dependency, leaked keys or social engineering.
- OUT OF SCOPE, reject on sight: tests, benches, test_utils, mocks, noop impls, vendored libmdbx, networking crates, public JSON-RPC namespaces, CLI, docs, Cargo files; denial of service, resource exhaustion, unbounded memory or cache growth, rate limiting, timeouts, slow paths, load-dependent crashes; defects inside revm, alloy or c-kzg with no path through this repo; centralization risk; best-practice notes; publicly known issues; theoretical findings.
- The impact must be one of: Critical - reth accepts a block the spec rejects or rejects a block the spec accepts (consensus split), a state root, receipts root or balance differing from the spec, a deterministic panic or wrong verdict on every reth node from one tx or block; High - reth-built blocks rejected by other clients, a valid chain marked invalid or a wrong latest_valid_hash stalling nodes until manual intervention, pool admission diverging from block validity, non-deterministic execution between cached / prewarmed / JIT / parallel and serial paths.
- Focus on real impact: a block, root, read or verdict that differs from the spec.

## Validate
- Write the equality the question claims is broken between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's input and record every read and write of the header fields, `transactions`, `withdrawals`, `requests`, `blob_versioned_hashes`, the `SpecId` / `EvmEnv`, `HashedPostState`, `TrieUpdates`, `ExecutionCache` / `TxPoolPrewarmCacheSnapshot` / overlay entries, `receipts`, `gas_used` and the returned `PayloadStatus`.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `ensure_well_formed_payload`, `validate_header` / `validate_header_against_parent`, `validate_block_pre_execution_with_tx_root`, `validate_block_post_execution`, the state-root comparison in `validate_post_execution`, `InvalidHeaderCache`, the pool's `validate_stateless` / `validate_stateful`, or revm's own checks already prevent the divergence.
- State what the attacker gains per block or transaction and how many nodes it hits.
- Require exact file/function support and a reproducible Rust test using an in-memory or dev-chain provider.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken equality, the code path, root cause, the attacker's exact input, exploit flow, and why existing guards fail]

### Impact Explanation
[Which verdict, root, read or built block differs, from which parties' view, how many nodes, matching severity category]

### Likelihood Explanation
[Preconditions, fork and chain state required, attacker cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo nextest test plan with the exact assertions on both sides of the equality]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for reth claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A claim is only valid if the report states the broken equality between two named values and shows both sides concretely. Reject prose-only claims.
- Reject anything requiring a malicious peer, node, RPC caller, consensus layer or operator, a misconfiguration flag, a compromised dependency, another user's key, leaked keys or social engineering.
- OUT OF SCOPE, reject on sight: tests, benches, test_utils, mocks, noop impls, vendored libmdbx, networking crates, public JSON-RPC namespaces, CLI, docs, Cargo files; denial of service, resource exhaustion, unbounded memory or cache growth, rate limiting, timeouts, slow paths, load-dependent crashes; defects inside revm, alloy or c-kzg with no path through this repo; centralization risk; best-practice notes; feature requests; publicly known issues; theoretical findings.
- The impact must be one of: Critical - reth accepts a block the spec rejects or rejects a block the spec accepts (consensus split), a state root, receipts root or balance differing from the spec, a deterministic panic or wrong verdict on every reth node from one tx or block; High - reth-built blocks rejected by other clients, a valid chain marked invalid or a wrong latest_valid_hash stalling nodes until manual intervention, pool admission diverging from block validity, non-deterministic execution between cached / prewarmed / JIT / parallel and serial paths.
- Reject claims where the only loss is the attacker's own funds or their own node.
- Reject if the bug was already fixed, publicly disclosed, or covered by a known-issues list.
- A valid report must be triggerable by an unprivileged party against the current code through a transaction, bytecode, or a permissionlessly proposed block delivered by an honest consensus layer.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function/method/struct, and line references.
2. The equality written explicitly, with both sides shown before and after.
3. Clear root cause: which validation gap, root or cache mismatch, wrong fork rule, wrong state read, or forkchoice error causes it.
4. Reachable exploit path: preconditions -> attacker input -> engine tree, executor, trie or pool sequence -> observed divergence.
5. `ensure_well_formed_payload`, header and parent validation, pre- and post-execution validation, the state-root comparison, `InvalidHeaderCache`, pool validation and revm's own checks reviewed and shown insufficient.
6. Impact stated concretely: which verdict, root or block differs, from whose view, how many nodes, and whether it is repeatable.
7. Reproducible proof: cargo nextest test on an in-memory or dev-chain provider, with the asserted values.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can an ordinary user's transaction or a permissionless proposer's block trigger it with no privileged role and no malicious peer?
- Is the flaw in this repo's code, not in revm, alloy or c-kzg?
- Which verdict, root, read or built block differs, for how many nodes, and can it be repeated?
- Would the Ethereum Foundation bug bounty triager accept the exploit path for the reth execution client?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken equality and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[Which verdict, root, read or built block differs, affected nodes, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, fork and chain state required, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or cargo nextest test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for reth.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repo context only (`crates/consensus/**`, `crates/payload/**`, `crates/ethereum/**`, `crates/engine/{{primitives,tree,execution-cache}}/**`, `crates/rpc/rpc-engine-api/**`, `crates/evm/**`, `crates/revm/**`, `crates/chainspec/**`, `crates/chain-state/**`, `crates/transaction-pool/**`, `crates/trie/**`, `crates/storage/{{provider,storage-overlay}}/**`, excluding tests, benches, test_utils, mocks and noop impls). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged analogs that break an equality: a block reth accepts that the spec rejects or vice versa, a state or receipts root that differs from a full recompute, a state read that differs from the parent's committed post-state, a cached / prewarmed / JIT / parallel output that differs from serial execution, a reth-built block that fails its own validation, a fork rule set that differs between execution, validation, pool and spec, or a valid chain marked invalid.
- OUT OF SCOPE, reject on sight: tests, benches, test_utils, mocks, vendored libmdbx, networking crates, public JSON-RPC namespaces, CLI, docs; denial of service, resource exhaustion, unbounded memory or cache growth, rate limiting, timeouts, load-dependent crashes; defects inside revm, alloy or c-kzg with no path here; anything requiring a malicious peer, node, RPC caller, consensus layer, operator or flag; centralization risk; best-practice notes; publicly known issues; theoretical findings.
- The impact must be one of: Critical - consensus split, a state root, receipts root or balance differing from the spec, a deterministic panic or wrong verdict on every reth node from one tx or block; High - reth-built blocks rejected by other clients, a valid chain marked invalid or a wrong latest_valid_hash stalling nodes until manual intervention, pool admission diverging from block validity, non-deterministic execution between cached / prewarmed / JIT / parallel and serial paths.
- Reject analogs where the only loss is the attacker's own funds or their own node.

## Validate
- Map the bug class to the strongest reachable path in this repo and state the equality it would break.
- Evaluate both sides before and after the attacker's transaction or block.
- Prove root cause with exact file/function support.
- Accept only a concrete wrong verdict, wrong root, wrong read, non-deterministic output, invalid built block, wrong fork rule set, or wrongly invalidated chain.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
