import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'chainwayxyz/citrea'
# todo: the name of the repository
REPO_NAME = 'citrea'

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
    # LENS: FROM A BYTE AN ORDINARY USER CHOOSES TO cBTC LEAVING THE BRIDGE AND TO A
    # PROOF CLEMENTINE'S OPERATORS BET THEIR COLLATERAL ON.
    # Citrea is a Bitcoin rollup: Bitcoin is the DA layer and the settlement layer, and
    # the only thing that makes an L2 state root true is a batch proof plus a light
    # client proof, both of which the Clementine bridge circuit consumes. Untrusted bytes
    # enter through four doors an unprivileged party fully controls: an EVM transaction
    # sent to the public sequencer or full-node RPC, a `citrea_sendRawDepositTransaction`
    # blob that becomes a `SYSTEM_SIGNER` call to the Bridge contract, any Bitcoin
    # transaction whose wtxid carries the reveal prefix (chunks, commitments, method-id
    # bodies, malformed reveals), and a JSON-RPC request to a node. Those bytes end in
    # three places: cBTC balances the Bridge system contract controls, a
    # `BatchProofCircuitOutput` / `LightClientCircuitOutput` journal, and the state root
    # honest full nodes converge on. A file belongs here only if a custody, proof
    # soundness, determinism or authority binding must hold across it.
    # =================================================================================

    # -- The public front doors: RPC surfaces anyone on the internet can reach ------------
    # `citrea_sendRawDepositTransaction` takes arbitrary bytes and `Auth` protects exactly
    # three methods, taking the API key from the last positional param.
    "crates/sequencer/src/rpc.rs",
    "crates/sequencer/src/deposit_data_mempool.rs",
    "crates/sequencer/src/mempool.rs",
    "crates/sequencer/src/tx_validator.rs",
    "crates/fullnode/src/rpc.rs",
    "crates/batch-prover/src/rpc.rs",
    "crates/light-client-prover/src/rpc.rs",
    "crates/common/src/rpc/auth.rs",
    "crates/common/src/rpc/server.rs",
    "crates/common/src/rpc/mod.rs",
    "crates/common/src/rpc/utils.rs",
    "crates/common/src/rpc/eip_7966.rs",
    "crates/sovereign-sdk/full-node/sov-ledger-rpc/src/server.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs",

    # -- Ethereum RPC: what a node tells the world about state it has not proved ----------
    "crates/ethereum-rpc/src/ethereum.rs",
    "crates/ethereum-rpc/src/lib.rs",
    "crates/ethereum-rpc/src/trace.rs",
    "crates/ethereum-rpc/src/subscription.rs",
    "crates/ethereum-rpc/src/gas_price/gas_oracle.rs",
    "crates/ethereum-rpc/src/gas_price/fee_history.rs",
    "crates/ethereum-rpc/src/gas_price/cache.rs",
    "crates/ethereum-rpc/src/gas_price/mod.rs",
    "crates/evm/src/rpc_helpers/filter.rs",
    "crates/evm/src/rpc_helpers/log_utils.rs",
    "crates/evm/src/rpc_helpers/tracing_utils.rs",
    "crates/evm/src/rpc_helpers/mod.rs",
    "crates/evm/src/query.rs",
    "crates/evm/src/provider_functions.rs",

    # -- EVM execution: where cBTC moves and where native and zk must agree ---------------
    # `SYSTEM_SIGNER` bypasses, the L1 fee charged in `handler.rs`, the Bridge / Bitcoin
    # light client system contracts, and the schnorr precompile the bridge relies on.
    "crates/evm/src/call.rs",
    "crates/evm/src/hooks.rs",
    "crates/evm/src/genesis.rs",
    "crates/evm/src/lib.rs",
    "crates/evm/src/signer/mod.rs",
    "crates/evm/src/evm/call.rs",
    "crates/evm/src/evm/executor.rs",
    "crates/evm/src/evm/handler.rs",
    "crates/evm/src/evm/mod.rs",
    "crates/evm/src/evm/db.rs",
    "crates/evm/src/evm/db_commit.rs",
    "crates/evm/src/evm/db_init.rs",
    "crates/evm/src/evm/conversions.rs",
    "crates/evm/src/evm/primitive_types.rs",
    "crates/evm/src/evm/system_contracts/mod.rs",
    "crates/evm/src/evm/system_events.rs",
    "crates/evm/src/evm/precompiles/mod.rs",
    "crates/evm/src/evm/precompiles/schnorr.rs",

    # -- Block production and the rules an L2 block must obey -----------------------------
    "crates/sequencer/src/runner.rs",
    "crates/sequencer/src/da.rs",
    "crates/sequencer/src/db_provider/mod.rs",
    "crates/sequencer/src/types.rs",
    "crates/sequencer/src/utils.rs",
    "crates/sequencer/src/lib.rs",
    "crates/sequencer/src/commitment/controller.rs",
    "crates/sequencer/src/commitment/service.rs",
    "crates/sequencer/src/commitment/helpers.rs",
    "crates/sequencer/src/commitment/mod.rs",
    "crates/l2-block-rule-enforcer/src/call.rs",
    "crates/l2-block-rule-enforcer/src/hooks.rs",
    "crates/l2-block-rule-enforcer/src/genesis.rs",
    "crates/l2-block-rule-enforcer/src/query.rs",
    "crates/l2-block-rule-enforcer/src/lib.rs",

    # -- The state transition function, applied natively and inside the batch circuit ------
    "crates/citrea-stf/src/verifier.rs",
    "crates/citrea-stf/src/hooks_impl.rs",
    "crates/citrea-stf/src/runtime.rs",
    "crates/citrea-stf/src/genesis_config.rs",
    "crates/citrea-stf/src/lib.rs",
    "crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/lib.rs",
    "crates/sovereign-sdk/module-system/sov-modules-stf-blueprint/src/stf_blueprint.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/storage/codec.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/runtime/capabilities.rs",
    "crates/sovereign-sdk/module-system/sov-modules-core/src/common/address.rs",
    "crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs",
    "crates/sovereign-sdk/module-system/sov-modules-api/src/default_context.rs",
    "crates/sovereign-sdk/module-system/sov-modules-api/src/containers/map.rs",
    "crates/sovereign-sdk/module-system/sov-modules-api/src/containers/value.rs",
    "crates/sovereign-sdk/module-system/sov-modules-api/src/containers/vec.rs",
    "crates/sovereign-sdk/module-system/sov-modules-api/src/containers/offchain_map.rs",
    "crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs",
    "crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs",
    "crates/sovereign-sdk/module-system/sov-state/src/codec/borsh_codec.rs",
    "crates/sovereign-sdk/module-system/sov-state/src/codec/bcs_codec.rs",
    "crates/sovereign-sdk/module-system/sov-state/src/codec/rlp_codec.rs",
    "crates/sovereign-sdk/module-system/sov-state/src/lib.rs",
    "crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs",
    "crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs",
    "crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/lib.rs",
    "crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/genesis.rs",

    # -- Bitcoin as DA: what a blob is, who sent it, and completeness of a block -----------
    # Any funded party can inscribe a prefix-matching reveal; `BitcoinVerifier` and the
    # parsers are the only thing separating that from protocol data.
    "crates/bitcoin-da/src/verifier.rs",
    "crates/bitcoin-da/src/helpers/parsers.rs",
    "crates/bitcoin-da/src/helpers/merkle_tree.rs",
    "crates/bitcoin-da/src/helpers/mod.rs",
    "crates/bitcoin-da/src/helpers/builders/body_builders.rs",
    "crates/bitcoin-da/src/helpers/builders/mod.rs",
    "crates/bitcoin-da/src/service.rs",
    "crates/bitcoin-da/src/monitoring.rs",
    "crates/bitcoin-da/src/utxo_manager.rs",
    "crates/bitcoin-da/src/tx_signer.rs",
    "crates/bitcoin-da/src/fee.rs",
    "crates/bitcoin-da/src/rpc.rs",
    "crates/bitcoin-da/src/network_constants.rs",
    "crates/bitcoin-da/src/error.rs",
    "crates/bitcoin-da/src/lib.rs",
    "crates/bitcoin-da/src/spec/blob.rs",
    "crates/bitcoin-da/src/spec/block.rs",
    "crates/bitcoin-da/src/spec/header.rs",
    "crates/bitcoin-da/src/spec/proof.rs",
    "crates/bitcoin-da/src/spec/short_proof.rs",
    "crates/bitcoin-da/src/spec/transaction.rs",
    "crates/bitcoin-da/src/spec/address.rs",
    "crates/bitcoin-da/src/spec/block_hash.rs",
    "crates/bitcoin-da/src/spec/utxo.rs",
    "crates/bitcoin-da/src/spec/mod.rs",
    "crates/short-header-proof-provider/src/lib.rs",
    "crates/short-header-proof-provider/src/native.rs",
    "crates/short-header-proof-provider/src/zk.rs",

    # -- The light client circuit: the one output Clementine's bridge circuit trusts -------
    "crates/light-client-prover/src/circuit/mod.rs",
    "crates/light-client-prover/src/circuit/accessors.rs",
    "crates/light-client-prover/src/circuit/method_id_verifier.rs",
    "crates/light-client-prover/src/circuit/initial_values.rs",
    "crates/light-client-prover/src/da_block_handler.rs",
    "crates/light-client-prover/src/input_builder.rs",
    "crates/light-client-prover/src/lcp_storage.rs",
    "crates/light-client-prover/src/services.rs",
    "crates/light-client-prover/src/lib.rs",
    "guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs",

    # -- The batch prover and the guest that produces the state-transition journal ---------
    "crates/batch-prover/src/prover.rs",
    "crates/batch-prover/src/l1_syncer.rs",
    "crates/batch-prover/src/l2_syncer.rs",
    "crates/batch-prover/src/partition.rs",
    "crates/batch-prover/src/lib.rs",
    "crates/prover-services/src/parallel.rs",
    "crates/prover-services/src/lib.rs",
    "crates/risc0/src/guest.rs",
    "crates/risc0/src/host/local.rs",
    "crates/risc0/src/host/mod.rs",
    "crates/risc0/src/lib.rs",
    "guests/risc0/batch-proof/bitcoin/src/bin/batch_proof_bitcoin.rs",
    "bin/citrea/src/guests.rs",
    "bin/citrea/src/rollup/bitcoin.rs",
    "bin/citrea/src/rollup/mod.rs",
    "bin/citrea/src/eth.rs",
    "bin/citrea/src/lib.rs",

    # -- Full node: the honest party that must converge on the proved chain ----------------
    "crates/fullnode/src/da_block_handler.rs",
    "crates/fullnode/src/l2_syncer.rs",
    "crates/fullnode/src/lib.rs",
    "crates/fullnode/src/error.rs",
    "crates/common/src/da.rs",
    "crates/common/src/l2.rs",
    "crates/common/src/cache.rs",
    "crates/common/src/utils.rs",
    "crates/common/src/config/mod.rs",
    "crates/common/src/config/rpc.rs",
    "crates/common/src/config/risc0.rs",
    "crates/common/src/lib.rs",

    # -- Persisted protocol truth and the types the proofs commit to -----------------------
    "crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/mod.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/state_db.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/native_db.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/schema/tables.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/schema/types/l2_block.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/schema/types/batch_proof.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/schema/types/light_client_proof.rs",
    "crates/sovereign-sdk/full-node/db/sov-db/src/schema/types/mod.rs",
    "crates/sovereign-sdk/full-node/sov-prover-storage-manager/src/lib.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/da.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/stf.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/transaction.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/witness.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/batch_proof/input/v3.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/batch_proof/output/v3.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/batch_proof/mod.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/light_client_proof/input.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/light_client_proof/output.rs",
    "crates/sovereign-sdk/rollup-interface/src/state_machine/zk/light_client_proof/mod.rs",
    "crates/sovereign-sdk/rollup-interface/src/spec.rs",
    "crates/sovereign-sdk/rollup-interface/src/fork/mod.rs",
    "crates/sovereign-sdk/rollup-interface/src/fork/manager.rs",
    "crates/sovereign-sdk/rollup-interface/src/fork/migration.rs",

    # -- Protocol constants, forks and shared primitives -----------------------------------
    "crates/primitives/src/forks.rs",
    "crates/primitives/src/basefee.rs",
    "crates/primitives/src/compression.rs",
    "crates/primitives/src/merkle.rs",
    "crates/primitives/src/types.rs",
    "crates/primitives/src/constants.rs",
    "crates/primitives/src/zk_dev_mode.rs",
    "crates/primitives/src/lib.rs",

    # =================================================================================
    # NOT IN THIS VARIANT:
    # * **/tests/**, **/tests.rs, `test_utils.rs`, `crates/evm/src/smart_contracts/**`,
    #   mock-da and mock-zkvm adapters, `bin/citrea/src/rollup/mock.rs` and the mock guest
    #   binaries - tests, fixtures and mocks.
    # * `**/build.rs`, `sov-modules-macros/**`, `guests/*/src/lib.rs` (1-line includes),
    #   `elfs`/generated method-id artefacts.
    # * `*.toml`, `*.md`, `docs/**`, `devops/**`, `docker/**`, `nix/**`, `resources/**`,
    #   `audits/**`, `proving-stats/**` - configuration, documentation, tooling.
    # * `**/metrics.rs`, `**/db_migrations/**`, `crates/storage-ops/**`, `bin/cli/**`,
    #   `crates/common/src/backup/**`, `crates/sp1/**` - telemetry, operator-only
    #   maintenance tooling and an unused prover backend; no unprivileged decision.
    # =================================================================================
]


target_scopes = [
    "Critical. ARBITRARY BYTES FROM THE INTERNET BECOME A SYSTEM TRANSACTION. `citrea_sendRawDepositTransaction` accepts a `Bytes` blob from any unauthenticated caller, `DepositDataMempool::make_deposit_tx_from_data` wraps it in `BridgeWrapper::deposit(...)` with `from: SYSTEM_SIGNER` and `SYSTEM_TX_GAS_LIMIT`, an `eth_call` simulation is the only admission gate, and de-duplication is `DepositDataMempool::calc_tx_id` over the attacker's own bytes in `pending_deposits`; `fetch_deposits` / `remove_deposits` then decide what `runner` puts in the block. Show a blob shaped so the simulation passes but execution mints or credits cBTC the Clementine vault never received, or so a legitimate deposit is displaced, replayed or permanently blocked from ever being included. Binding: cBTC credited by a `SYSTEM_SIGNER` Bridge deposit == exactly one Bitcoin move-to-vault output Clementine actually presigned, counted once.",

    "Critical. THE LIGHT CLIENT CIRCUIT TAKES CHUNKS FROM ANYONE. In `LightClientProofCircuit::run_l1_block`, `DataOnDa::Complete`, `DataOnDa::Aggregate` and `DataOnDa::SequencerCommitment` compare `blob.sender()` against `batch_prover_da_public_key` / `sequencer_da_public_key`, but `DataOnDa::Chunk` is inserted straight into the JMT by `ChunkAccessor::insert(blob.wtxid(), chunk)` with no sender check, and an aggregate is reassembled by looking those wtxids up under only a `MAX_COMPRESSED_BLOB_SIZE` bound. Show an unprivileged party who inscribes prefix-matching Bitcoin reveals so that the bytes an honest aggregate resolves to are not the bytes the batch prover chunked - a wtxid the attacker can land first, a chunk that changes the decompressed proof, or a body that makes `process_complete_proof` accept a state transition the prover never produced. Binding: the complete proof `process_complete_proof` verifies == the byte string the batch prover signed and chunked.",

    "Critical. TWO HONEST PROVERS, ONE L1 BLOCK, TWO JOURNALS. The light client proof must be a pure function of the DA block, yet `run_l1_block` walks attacker-influenced blobs with `continue` / `continue 'blob_loop` skips, reads and writes shared JMT state through `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor` and `BatchProofMethodIdAccessor` while iterating, and finishes with a `while let` chaining loop over commitment indices. Show a set of Bitcoin transactions an unprivileged party inscribes into one L1 block such that two honest light client provers - differing only in what their `input_builder` gathered, in blob ordering, or in prior JMT contents - commit different `LightClientCircuitOutput` values for the same block. Binding: `LightClientCircuitOutput` for L1 block H == the same value for every honest prover, independent of attacker-supplied blobs.",

    "Critical. A METHOD-ID BODY NOBODY CHECKED THE SENDER OF. `DataOnDa::BatchProofMethodId` is processed in `run_l1_block` with no `blob.sender()` check at all: any funded party can inscribe one. The only gates are `activation_l2_height > last_activation_height`, `citrea_network_to_chain_id(network) == body.chain_id`, and `verify_method_id_security_council`, which reads pubkey indices out of the attacker-supplied `signatures_with_index()`, bounds them with `index >= 5`, requires strictly ascending indices, and calls `Signature::from_bytes` + `verify_prehash` over `eip191_hash_message(body.serialize())`. Show an unprivileged inscriber who gets `BatchProofMethodIdAccessor::insert` to run for a method id the council never authorised - malleated or re-encoded signature bytes, a body serialisation collision, a replayed council body at a shifted activation height - or who front-runs the real upgrade so the genuine one is skipped as not greater. Binding: every `(activation_l2_height, method_id)` in the accessor == a body three distinct council keys signed for this chain, applied once at the height they signed.",

    "Critical. THE BATCH PROOF PROVES SOMEONE ELSE'S BLOCKS. `StateTransitionVerifier::run_sequencer_commitments_in_da_slot` reads `BatchProofCircuitInputV3Part1` from the host and hands `data.sequencer_commitments`, `data.previous_sequencer_commitment` and `data.prev_hash_proof` to `apply_l2_blocks_from_sequencer_commitments`, which checks L2 blocks against `sequencer_public_key`; the emitted `BatchProofCircuitOutputV3` carries `sequencer_commitment_index_range`, `sequencer_commitment_hashes`, `previous_commitment_index` and `previous_commitment_hash`, and `LightClientProofCircuit::verify_batch_proof_seq_comm_relation` is what ties those to what the light client already stored. Show an unprivileged party whose inscribed or replayed commitment data makes a proof chain accepted across a gap, a reordering or a duplicated index - so `last_l2_state_root` advances to a root no contiguous run of sequencer-signed commitments produced. Binding: the commitment index range a light client accepts == a gapless run of indices the sequencer signed, each hash counted once.",

    "Critical. DA COMPLETENESS: WHAT THE CIRCUIT SEES VERSUS WHAT THE BLOCK CONTAINS. `BitcoinVerifier::verify_transactions` filters `inclusion_proof.wtxids` by `reveal_tx_prefix`, `zip_eq`s them against the completeness proof, recomputes `calculate_wtxid`, and only then calls `parse_relevant_transaction`; the segwit commitment is validated with `MINIMUM_WITNESS_COMMITMENT_SIZE` and `WITNESS_COMMITMENT_PREFIX`, and `ParserError` decides what is dropped. Show a Bitcoin transaction an unprivileged party can mine into a real block that the native `da_block_handler` and the in-circuit verifier disagree about - a reveal accepted on one side and rejected on the other, a wtxid the filter matches but the parser silently drops, a coinbase whose commitment structure passes one check and not the other. Binding: the blob set the circuit derives from block B == the blob set an honest full node acted on for block B.",

    "Critical. NATIVE EXECUTION AND CIRCUIT EXECUTION MUST PRODUCE THE SAME ROOT. A single EVM transaction an attacker sends to the public sequencer RPC flows through `CitreaTransactionValidator::validate_transaction`, `citrea_evm::call`, the Citrea `handler` that prices `l1_diff_size` and `TxInfo::l1_fee`, `db_commit`, and the `WorkingSet` / `ReadWriteLog` recorded in `scratchpad.rs`, then is replayed inside the guest against `ZkStorage` using only the witness. Show a transaction whose native effect differs from its in-circuit effect - a value the witness does not pin, a fee or state-diff computation that depends on something not in the log, a fork boundary read through `fork_from_block_number` - so the sequencer's chain either becomes unprovable or a proof commits a root the honest full node did not compute. Binding: the state root `ProverStorage` produces natively for L2 block N == the root the batch circuit produces for L2 block N.",

    "Critical. THE L1 HASH THE PROOF SWEARS TO. `run_sequencer_commitments_in_da_slot` reports `last_l1_hash_on_bitcoin_light_client_contract` from `SHORT_HEADER_PROOF_PROVIDER.take_last_queried_hash()` when any short header proof was queried, and otherwise from `get_last_l1_hash_on_contract`, which replays a JMT read of the `BitcoinLightClient` slot through `last_l1_hash_witness`; the queries themselves come from `ZkShortHeaderProofProviderService::get_and_verify_short_header_proof_by_l1_hash`, reachable from any EVM transaction that touches the light client system contract. Show an unprivileged transaction that steers which hash is reported - by forcing or suppressing a short-header query, by querying a hash the contract never recorded, or by exploiting the cache-versus-storage split in `get_last_l1_hash_on_contract` - so the light client accepts a batch proof anchored to an L1 block it never verified. Binding: the L1 hash in the batch proof output == a Bitcoin block hash present in the light client's own chain of `BlockHashAccessor` entries.",

    "Critical. cBTC CONSERVATION INSIDE THE EVM. Every cBTC in existence was minted by a Bridge deposit, and every unit spent as fees is meant to reach the base-fee, L1-fee and priority-fee vaults. `system_events::create_system_transactions` / `signed_system_transaction` construct transactions under `SYSTEM_SIGNER`, `is_system_caller` in `handler.rs` exempts them, `evm/hooks.rs` runs `BitcoinLightClient::set_block_info` and deposits at block boundaries, and `evm/call.rs` admits ordinary user transactions on the same path. Show an ordinary EVM transaction that is treated as a system caller, that escapes the L1 fee charged in the Citrea handler, that survives a revert with balances applied, or that reaches `BridgeWrapper::deposit` / `WCBTC` accounting so supply changes without a Bitcoin-side event. Binding: total cBTC supply after block N == supply after block N-1 plus deposits minted minus withdrawals and burns.",

    "Critical. THE MISSING BINDING - what nobody built. Nothing in this repository ties a deposit blob accepted over the public RPC back to an on-chain Bitcoin move-to-vault output before it executes as a `SYSTEM_SIGNER` transaction; nothing authenticates the sender of a `DataOnDa::Chunk` or a `DataOnDa::BatchProofMethodId` inscription; nothing guarantees that two honest light client provers reading the same L1 block reach the same journal; and nothing re-derives, at proof-verification time, that the state root a batch proof commits to is the root an honest full node independently computed. Identify the FIRST point at which a byte an unprivileged party chose - an EVM transaction, a deposit blob, an inscribed Bitcoin reveal, a JSON-RPC request - becomes minted cBTC, a stored JMT entry, an accepted method id, or a committed proof journal with no independent party ever re-deriving it. Prove it with one `cargo test` asserting both the value used and the value that should have authorised it, and show that once they diverge nothing in the protocol reconciles them.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate rollup-soundness audit questions for one Citrea target.

    ```
    target_file format:
    "'File Name: crates/light-client-prover/src/circuit/mod.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate custody and proof-soundness security audit questions for this exact
    Citrea target:

    {target_file}

    Project focus:
    Citrea is a Bitcoin rollup that uses Bitcoin for data availability and settlement, and
    whose proofs Clementine's BitVM bridge operators bet collateral on. Untrusted bytes
    enter through four doors: an EVM transaction sent to the public sequencer or full-node
    RPC, a `citrea_sendRawDepositTransaction` blob that becomes a `SYSTEM_SIGNER` call to
    the Bridge system contract, any Bitcoin transaction whose wtxid carries the reveal
    prefix (chunks, commitments, method-id bodies, malformed reveals - anyone can inscribe
    one), and any JSON-RPC request to a node. Those bytes end in three places: cBTC
    balances controlled by the Bridge and vault system contracts, a `BatchProofCircuitOutput`
    or `LightClientCircuitOutput` journal, and the state root honest full nodes converge on.
    Anything that moves cBTC, makes a false claim provable, makes a true claim unprovable,
    or makes two honest parties disagree - without the protocol re-deriving the fact
    independently - is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (module, struct, enum, fn, const, field) as they appear in the file.
    * EVERY question must close on a binding that must hold across a call. State it explicitly
      as an equality between two named values. Narrative questions are rejected.
    * Attacker is unprivileged only: anyone who can send an EVM transaction or a JSON-RPC
      request to a public Citrea endpoint, call `citrea_sendRawDepositTransaction` with bytes
      of their choosing, deploy and call contracts on L2, pay Bitcoin fees to inscribe or mine
      any Bitcoin transaction, and run their own full node or light client prover.
    * Attacker is NOT the sequencer, batch prover, light client prover operator, security
      council member, node operator or Clementine verifier/operator/aggregator. They hold no
      `sequencer_da_public_key`, `batch_prover_da_public_key`, council key, `SYSTEM_SIGNER`
      key, node API key or admin authority. No malicious peer or node, no key compromise, no
      majority hashrate, no TLS interception, no local or physical access, no compromised
      dependency, no social engineering.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - Tests, mocks and fixtures (`**/tests/**`, `**/tests.rs`, `test_utils.rs`,
        `crates/evm/src/smart_contracts/**`, mock-da, mock-zkvm, mock guests), generated and
        build files (`**/build.rs`, `sov-modules-macros/**`, elf/method-id artefacts),
        `*.toml`, `*.md`, `docs/**`, `devops/**`, `docker/**`, `**/metrics.rs`,
        `**/db_migrations/**` are OUT OF SCOPE.
      - Denial of service, rate limiting, retry/backoff, queue depth, resource exhaustion,
        unbounded collections, memory hygiene and log volume are OUT OF SCOPE.
      - Any scenario needing a dishonest sequencer, prover, node operator, security council
        member or Clementine role is OUT OF SCOPE, as is anything that only costs the
        attacker their own cBTC or BTC.
      - Defects in third-party crates (revm, reth, alloy, risc0, bitcoin, secp256k1, k256,
        jmt, rocksdb, jsonrpsee) with no exploit path through this repository's own code are
        OUT OF SCOPE, as are Solidity system-contract bugs and Clementine-side bugs.
      - Also excluded: leaked keys, best-practice notes, feature requests, missing headers,
        and theoretical findings with no demonstration.
      - A weakness in this repository that manipulates a third-party crate into unsafe
        behaviour remains fully in scope.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: cBTC minted, credited or moved without a matching Bitcoin-side deposit, or a
      withdrawal replayed or duplicated; user or vault funds permanently frozen; a batch
      proof or light client proof accepted for a state transition that did not happen, or a
      true transition made permanently unprovable; a split in the light client proof, where
      two honest provers reading the same L1 block commit different outputs; a forged DA
      inclusion or completeness result that hides or injects a blob; a sequencer commitment
      or method-id upgrade accepted that the signing authority never authorised; honest full
      nodes converging on a state root the proved chain does not contain.
      High: an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`; an
      honest node persisting or serving state that contradicts the proved chain and
      recoverable only by resync; a fork or activation boundary applied at different heights
      by nodes following the same rules.
    * Every question must be a concrete real-world scenario an unprivileged attacker can
      execute against a running Citrea deployment - an EVM transaction they sign, a deposit
      blob they submit, a Bitcoin transaction they inscribe, an RPC request they send. No
      speculative resource-hygiene or memory questions.
    * A panic or error is a finding only when it makes a true state transition unprovable,
      freezes funds, splits honest parties, or lets an unauthorised mint or state root
      through - say which.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable by a `cargo test` in this workspace (regtest bitcoind,
      the sequencer/full-node test harness, or a direct circuit or verifier call), with no
      mainnet and no live Clementine.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are: the
      cBTC minted and the Bitcoin deposited, the blob set proved and the blob set in the
      block, the root computed natively and the root computed in the circuit, the journal one
      honest prover emits and the journal another emits, the authority that signed and the
      authority the code checked, the caller and the party a method is for.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a sequencer, prover, node-operator, security council or Clementine key,
      certificate or role.
    * A bug in a dependency, in a Solidity system contract, or in Clementine with no reachable
      path through this repository.
    * Fee estimation, mempool policy, propagation timing, or an attacker burning only their own
      funds with no protocol value moved and no honest party harmed.
    * Findings only reproducible in tests, mocks, fixtures or generated files.

    Core bindings (each question must close on one):
    * CUSTODY: cBTC credited or moved on L2 == a Bitcoin-side deposit or withdrawal that
      actually happened, counted exactly once.
    * PROOF SOUNDNESS: what a `BatchProofCircuitOutput` or `LightClientCircuitOutput` claims ==
      what actually happened on Bitcoin and in L2 execution.
    * DETERMINISM: the journal one honest prover emits for an L1 block == the journal every
      other honest prover emits for that block.
    * DA INTEGRITY: the blob set derived inside the circuit for a Bitcoin block == the blob set
      an honest full node acted on for that block.
    * AUTHORITY: a sequencer commitment, method-id upgrade or system transaction accepted == one
      the holder of the corresponding key actually authorised.
    * EXECUTION AGREEMENT: the state root produced natively == the state root produced in the
      guest for the same L2 block.

    Each question must include:
    1. target struct/fn;
    2. attacker action (a concrete EVM transaction, deposit blob, inscribed Bitcoin transaction,
       or RPC request with its fields);
    3. preconditions (network, fork activation, node role, existing chain or JMT state);
    4. call sequence through the code;
    5. the binding that breaks, written as an equality;
    6. scoped impact and whose funds, proof or chain view is affected;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: struct_or_fn] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, breaking the binding BINDING_EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: cargo test PARAMETERS asserting CUSTODY, PROOF_SOUNDNESS, DETERMINISM, DA_INTEGRITY, AUTHORITY, or EXECUTION_AGREEMENT.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a rollup-soundness Citrea exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: anyone who can send an EVM transaction or JSON-RPC request to a public Citrea endpoint, call `citrea_sendRawDepositTransaction` with bytes of their choosing, deploy and call L2 contracts, pay Bitcoin fees to inscribe or mine any Bitcoin transaction, and run their own node or prover. They are not the sequencer, batch prover, light client prover operator, security council member, node operator or a Clementine role, and hold no DA public key, `SYSTEM_SIGNER` key, council key or node API key.
- Reject malicious peers or nodes, key compromise, majority hashrate, TLS interception, local or physical access, compromised dependencies and social engineering.
- OUT OF SCOPE, reject on sight: tests, mocks and fixtures (`**/tests/**`, `**/tests.rs`, `test_utils.rs`, `crates/evm/src/smart_contracts/**`, mock-da, mock-zkvm, mock guests), generated and build files (`**/build.rs`, `sov-modules-macros/**`, elf and method-id artefacts), `*.toml`, `*.md`, `docs/**`, `devops/**`, `docker/**`, `**/metrics.rs`, `**/db_migrations/**`; denial of service, rate limiting, retry behaviour, resource exhaustion and memory hygiene; third-party crate, Solidity system-contract or Clementine-side defects with no path through this repository; best-practice notes; feature requests; theoretical findings with no demonstration.
- The impact must be one of: Critical - cBTC minted, credited or moved without a matching Bitcoin-side deposit, a withdrawal replayed or duplicated, funds permanently frozen, a batch or light client proof accepted for a state transition that did not happen or a true one made unprovable, a light client proof split where two honest provers commit different outputs for the same L1 block, a forged DA inclusion or completeness result, a sequencer commitment or method-id upgrade accepted that was never authorised, or honest full nodes converging on a root the proved chain does not contain; High - an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`, an honest node persisting or serving state contradicting the proved chain, or a fork boundary applied at different heights by nodes following the same rules.
- Focus on real impact: bridge value moving, a proof that lies, or honest parties splitting.

## Validate
- Write the binding the question claims is broken as an explicit equality between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's EVM transaction, deposit blob, inscribed Bitcoin transaction or RPC request, and record every read and write of the deposit blob and its `calc_tx_id`, `blob.sender()` and `blob.wtxid()`, the `DataOnDa` variant, the sequencer commitment index and hash, the batch proof method id and activation height, the short header proof and last L1 hash, the `WorkingSet` / `ReadWriteLog` entries and witness, and the resulting state root or journal.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `BitcoinVerifier::verify_transactions`, `parse_relevant_transaction`, the `blob.sender()` comparisons in `run_l1_block`, `verify_method_id_security_council`, `verify_batch_proof_seq_comm_relation`, `apply_l2_blocks_from_sequencer_commitments`, `CitreaTransactionValidator`, `is_system_caller`, `Auth`, the JMT witness or a fork rule already prevents the divergence.
- State what the attacker gains or destroys per attempt and whether it is repeatable across blocks, deposits or provers.
- Require exact file/fn support and a reproducible `cargo test` proof with no mainnet and no live Clementine.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken binding as an equality, the code path, root cause, the attacker's exact transaction, blob or request, exploit flow, and why existing guards fail]

### Impact Explanation
[What is minted, moved, frozen, proved or split, which party, repeatability, blast radius across blocks and nodes, matching severity category]

### Likelihood Explanation
[Preconditions, network and fork configuration, attacker cost in BTC/cBTC and fees, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo test plan with the exact assertions on both sides of the binding]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for Citrea claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A binding claim is only valid if the report states the broken equality between two named values and shows both sides concretely. Reject prose-only claims.
- Reject anything requiring a sequencer, batch prover, light client prover operator, node operator, security council or Clementine role, key or API key, a malicious peer or node, key compromise, majority hashrate, TLS interception, local or physical access, a compromised dependency, or social engineering.
- OUT OF SCOPE, reject on sight: tests, mocks and fixtures (`**/tests/**`, `**/tests.rs`, `test_utils.rs`, `crates/evm/src/smart_contracts/**`, mock-da, mock-zkvm, mock guests), generated and build files (`**/build.rs`, `sov-modules-macros/**`, elf and method-id artefacts), `*.toml`, `*.md`, `docs/**`, `devops/**`, `docker/**`, `**/metrics.rs`, `**/db_migrations/**`; denial of service, rate limiting, retry behaviour, resource exhaustion and memory hygiene; third-party crate, Solidity system-contract or Clementine-side defects with no path through this repository; best-practice notes; feature requests; theoretical findings with no demonstration.
- The impact must be one of: Critical - cBTC minted, credited or moved without a matching Bitcoin-side deposit, a withdrawal replayed or duplicated, funds permanently frozen, a batch or light client proof accepted for a state transition that did not happen or a true one made unprovable, a light client proof split across honest provers, a forged DA inclusion or completeness result, an unauthorised sequencer commitment or method-id upgrade, or honest full nodes converging on a root the proved chain does not contain; High - an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`, an honest node persisting or serving state contradicting the proved chain, or a fork boundary applied at different heights by nodes following the same rules.
- Reject claims that depend on a deployment ignoring the documented configuration, or that only harm the attacker's own funds.
- Reject if the bug was already fixed, publicly disclosed, or is covered by an existing advisory or CHANGELOG entry for a supported version.
- Reject a divergence with no custody, proof, determinism, DA-integrity or authority boundary crossed.
- A valid report must be triggerable by an unprivileged attacker against a Citrea deployment running the current release.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, struct/fn, and line references.
2. The binding written explicitly as an equality, with both sides shown before and after.
3. Clear root cause: which unverified user field, which missing sender or authority check, which unbound index, which non-deterministic read, which witness gap causes the divergence.
4. Reachable exploit path: preconditions -> attacker EVM transaction, deposit blob, inscribed Bitcoin transaction or RPC request -> call sequence -> observed divergence.
5. `BitcoinVerifier::verify_transactions`, `parse_relevant_transaction`, the `blob.sender()` checks in `run_l1_block`, `verify_method_id_security_council`, `verify_batch_proof_seq_comm_relation`, `apply_l2_blocks_from_sequencer_commitments`, `CitreaTransactionValidator`, `is_system_caller`, `Auth`, JMT witness verification and fork rules reviewed and shown insufficient.
6. Impact stated concretely: how much cBTC or BTC moves, whose, which proof or chain view breaks, and whether it is repeatable.
7. Reproducible proof: `cargo test` with the asserted values, no mainnet, no live Clementine.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can an ordinary depositor, L2 user, Bitcoin inscriber or internet caller trigger it with no role and no key?
- Is the flaw in this repository's code, not in a dependency, a Solidity contract, Clementine or a careless deployment?
- What value moves, which proof becomes false, or which honest parties split, and is it repeatable?
- Would a Citrea triager accept the exploit path?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken binding and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[What is minted, moved, frozen, proved or split, affected party, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, configuration, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or cargo test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for Citrea.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repository context only (`crates/*/src/**`, `bin/citrea/src/**`, `guests/risc0/**/src/bin/**`, excluding tests, mocks, generated and build files). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-attacker analogs that break a rollup binding: cBTC credited versus a Bitcoin deposit that actually happened, a proof journal versus what actually happened, one honest prover's journal versus another's for the same L1 block, the blob set proved versus the blob set in the block, an accepted commitment or method-id upgrade versus what the key holder authorised, the root computed natively versus the root computed in the guest.
- OUT OF SCOPE, reject on sight: tests, mocks and fixtures, generated and build files (`**/build.rs`, `sov-modules-macros/**`, elf and method-id artefacts), `*.toml`, `*.md`, `docs/**`, `devops/**`, `docker/**`, `**/metrics.rs`, `**/db_migrations/**`; denial of service, rate limiting, retry behaviour, resource exhaustion and memory hygiene; anything requiring a sequencer, prover, node operator, security council or Clementine role, key or API key, a malicious peer or node, key compromise, majority hashrate, TLS interception, local access or social engineering; third-party crate, Solidity system-contract or Clementine-side defects with no path through this repository; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - cBTC minted, credited or moved without a matching Bitcoin-side deposit, a withdrawal replayed or duplicated, funds permanently frozen, a false state transition proved or a true one made unprovable, a light client proof split across honest provers, a forged DA inclusion or completeness result, an unauthorised sequencer commitment or method-id upgrade, or honest nodes converging on an unproved root; High - an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`, an honest node serving state contradicting the proved chain, or a fork boundary applied at different heights.
- Reject analogs that depend on a deployment ignoring the documented configuration, and analogs with no custody, proof, determinism, DA-integrity or authority boundary crossed.

## Validate
- Map the bug class to the strongest reachable path in this repository and state the binding it would break as an equality.
- Evaluate both sides before and after the attacker's transaction, blob or request sequence.
- Prove root cause with exact file/fn support.
- Accept only concrete bridge value loss, a proof that accepts a false claim or rejects a true one, honest parties splitting, or an unauthorised commitment, upgrade or system transaction.

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
