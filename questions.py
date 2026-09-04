import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'stacks-network/stacks-core'
# todo: the name of the repository
REPO_NAME = 'stacks-core'

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
    # LENS: NAKAMOTO CONSENSUS AND CHAINSTATE INTEGRITY.
    # A Stacks block is accepted only if it descends correctly from a Bitcoin sortition,
    # carries a valid tenure change and coinbase, is signed by enough of the right signer
    # set, and produces the same state root on every node. The files below sit on the
    # path from attacker-influenced input - a submitted block, a burnchain commit, a
    # microblock poison, a fork - to one of three decisions: is this the one canonical
    # chain tip, does the state root committed equal the state every node computes, and
    # are block rewards paid exactly once to the miner who earned them. A question
    # belongs here only if it closes on an equality that must hold across block acceptance.
    # =================================================================================
    # -- clarity-types: Clarity value, type and effect model -------------------------------
    "clarity-types/src/effects/asset_map.rs",
    "clarity-types/src/effects/mod.rs",
    "clarity-types/src/errors/mod.rs",
    "clarity-types/src/lib.rs",
    "clarity-types/src/representations.rs",
    "clarity-types/src/types/mod.rs",
    "clarity-types/src/types/serialization.rs",
    "clarity-types/src/types/signatures.rs",
    "clarity-types/src/version.rs",

    # -- clarity: the Clarity language, analyser, interpreter, costs and database ----------
    "clarity/src/libclarity.rs",
    "clarity/src/vm/analysis/analysis_db.rs",
    "clarity/src/vm/analysis/arithmetic_checker/mod.rs",
    "clarity/src/vm/analysis/contract_interface_builder/mod.rs",
    "clarity/src/vm/analysis/errors.rs",
    "clarity/src/vm/analysis/mod.rs",
    "clarity/src/vm/analysis/read_only_checker/mod.rs",
    "clarity/src/vm/analysis/trait_checker/mod.rs",
    "clarity/src/vm/analysis/type_checker/contexts.rs",
    "clarity/src/vm/analysis/type_checker/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/contexts.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/assets.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/maps.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/options.rs",
    "clarity/src/vm/analysis/type_checker/v2_05/natives/sequences.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/contexts.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/assets.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/conversions.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/maps.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/mod.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/options.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/post_conditions.rs",
    "clarity/src/vm/analysis/type_checker/v2_1/natives/sequences.rs",
    "clarity/src/vm/analysis/types.rs",
    "clarity/src/vm/ast/definition_sorter/mod.rs",
    "clarity/src/vm/ast/errors.rs",
    "clarity/src/vm/ast/expression_identifier/mod.rs",
    "clarity/src/vm/ast/mod.rs",
    "clarity/src/vm/ast/parser/mod.rs",
    "clarity/src/vm/ast/parser/v1.rs",
    "clarity/src/vm/ast/parser/v2/lexer/error.rs",
    "clarity/src/vm/ast/parser/v2/lexer/mod.rs",
    "clarity/src/vm/ast/parser/v2/lexer/token.rs",
    "clarity/src/vm/ast/parser/v2/mod.rs",
    "clarity/src/vm/ast/stack_depth_checker.rs",
    "clarity/src/vm/ast/sugar_expander/mod.rs",
    "clarity/src/vm/ast/traits_resolver/mod.rs",
    "clarity/src/vm/ast/types.rs",
    "clarity/src/vm/callables.rs",
    "clarity/src/vm/clarity.rs",
    "clarity/src/vm/contexts.rs",
    "clarity/src/vm/contracts.rs",
    "clarity/src/vm/costs/constants.rs",
    "clarity/src/vm/costs/cost_functions.rs",
    "clarity/src/vm/costs/costs_1.rs",
    "clarity/src/vm/costs/costs_2.rs",
    "clarity/src/vm/costs/costs_2_testnet.rs",
    "clarity/src/vm/costs/costs_3.rs",
    "clarity/src/vm/costs/costs_4.rs",
    "clarity/src/vm/costs/costs_5.rs",
    "clarity/src/vm/costs/errors.rs",
    "clarity/src/vm/costs/execution_cost.rs",
    "clarity/src/vm/costs/mod.rs",
    "clarity/src/vm/database/caching/mod.rs",
    "clarity/src/vm/database/caching/weight_limited_fifo.rs",
    "clarity/src/vm/database/clarity_db.rs",
    "clarity/src/vm/database/clarity_store.rs",
    "clarity/src/vm/database/key_value_wrapper.rs",
    "clarity/src/vm/database/mod.rs",
    "clarity/src/vm/database/sqlite.rs",
    "clarity/src/vm/database/structures.rs",
    "clarity/src/vm/diagnostic.rs",
    "clarity/src/vm/errors.rs",
    "clarity/src/vm/events.rs",
    "clarity/src/vm/functions/arithmetic.rs",
    "clarity/src/vm/functions/assets.rs",
    "clarity/src/vm/functions/bitcoin.rs",
    "clarity/src/vm/functions/boolean.rs",
    "clarity/src/vm/functions/conversions.rs",
    "clarity/src/vm/functions/crypto.rs",
    "clarity/src/vm/functions/database.rs",
    "clarity/src/vm/functions/define.rs",
    "clarity/src/vm/functions/mod.rs",
    "clarity/src/vm/functions/options.rs",
    "clarity/src/vm/functions/post_conditions.rs",
    "clarity/src/vm/functions/principals.rs",
    "clarity/src/vm/functions/sequences.rs",
    "clarity/src/vm/functions/tuples.rs",
    "clarity/src/vm/hooks/internals.rs",
    "clarity/src/vm/hooks/mod.rs",
    "clarity/src/vm/hooks/trace.rs",
    "clarity/src/vm/mod.rs",
    "clarity/src/vm/representations.rs",
    "clarity/src/vm/resource_limiter.rs",
    "clarity/src/vm/tooling/mod.rs",
    "clarity/src/vm/types/mod.rs",
    "clarity/src/vm/types/serialization.rs",
    "clarity/src/vm/types/signatures.rs",
    "clarity/src/vm/variables.rs",
    "clarity/src/vm/version.rs",

    # -- stacks-codec: transaction and message wire encoding -------------------------------
    "stacks-codec/src/lib.rs",
    "stacks-codec/src/strings.rs",
    "stacks-codec/src/transaction.rs",

    # -- crates/stacks-transactions: standalone transaction and post-condition checks ------
    "crates/stacks-transactions/src/lib.rs",

    # -- stacks-common: addresses, hashing, secp256k1, codec and shared utils --------------
    "stacks-common/src/address/b58.rs",
    "stacks-common/src/address/c32.rs",
    "stacks-common/src/address/c32_old.rs",
    "stacks-common/src/address/mod.rs",
    "stacks-common/src/alloc_tracker.rs",
    "stacks-common/src/bitvec.rs",
    "stacks-common/src/codec/macros.rs",
    "stacks-common/src/codec/mod.rs",
    "stacks-common/src/libcommon.rs",
    "stacks-common/src/types/chainstate.rs",
    "stacks-common/src/types/mod.rs",
    "stacks-common/src/types/net.rs",
    "stacks-common/src/types/sqlite.rs",
    "stacks-common/src/util/chunked_encoding.rs",
    "stacks-common/src/util/db.rs",
    "stacks-common/src/util/ed25519.rs",
    "stacks-common/src/util/hash.rs",
    "stacks-common/src/util/log.rs",
    "stacks-common/src/util/lru_cache.rs",
    "stacks-common/src/util/macros.rs",
    "stacks-common/src/util/mod.rs",
    "stacks-common/src/util/pair.rs",
    "stacks-common/src/util/pipe.rs",
    "stacks-common/src/util/retry.rs",
    "stacks-common/src/util/secp256k1/mod.rs",
    "stacks-common/src/util/secp256k1/native.rs",
    "stacks-common/src/util/secp256k1/wasm.rs",
    "stacks-common/src/util/secp256r1.rs",
    "stacks-common/src/util/serde_serializers.rs",
    "stacks-common/src/util/uint.rs",
    "stacks-common/src/util/vrf.rs",

    # -- libsigner: signer transport, events and v0 messages -------------------------------
    "libsigner/src/error.rs",
    "libsigner/src/events.rs",
    "libsigner/src/http.rs",
    "libsigner/src/libsigner.rs",
    "libsigner/src/runloop.rs",
    "libsigner/src/session.rs",
    "libsigner/src/signer_set.rs",
    "libsigner/src/v0/messages.rs",
    "libsigner/src/v0/mod.rs",
    "libsigner/src/v0/signer_state.rs",

    # -- libstackerdb: StackerDB chunk signing and verification ----------------------------
    "libstackerdb/src/libstackerdb.rs",

    # -- pox-locking: the Rust side that locks and unlocks STX for PoX/stacking ------------
    "pox-locking/src/events.rs",
    "pox-locking/src/events_24.rs",
    "pox-locking/src/lib.rs",
    "pox-locking/src/pox_1.rs",
    "pox-locking/src/pox_2.rs",
    "pox-locking/src/pox_3.rs",
    "pox-locking/src/pox_4.rs",
    "pox-locking/src/pox_5.rs",

    # -- stacks-signer: the Nakamoto signer decision logic and chainstate view -------------
    "stacks-signer/src/chainstate/mod.rs",
    "stacks-signer/src/chainstate/v1.rs",
    "stacks-signer/src/chainstate/v2.rs",
    "stacks-signer/src/cli.rs",
    "stacks-signer/src/client/mod.rs",
    "stacks-signer/src/client/stackerdb.rs",
    "stacks-signer/src/client/stacks_client.rs",
    "stacks-signer/src/config.rs",
    "stacks-signer/src/lib.rs",
    "stacks-signer/src/main.rs",
    "stacks-signer/src/monitor_signers.rs",
    "stacks-signer/src/monitoring/mod.rs",
    "stacks-signer/src/monitoring/prometheus.rs",
    "stacks-signer/src/monitoring/server.rs",
    "stacks-signer/src/runloop.rs",
    "stacks-signer/src/signerdb.rs",
    "stacks-signer/src/utils.rs",
    "stacks-signer/src/v0/mod.rs",
    "stacks-signer/src/v0/signer.rs",
    "stacks-signer/src/v0/signer_state.rs",

    # -- stacks-node: the node binary, run loops, miner, burnchain and event dispatch ------
    "stacks-node/src/burnchains/bitcoin/core_controller.rs",
    "stacks-node/src/burnchains/bitcoin/mod.rs",
    "stacks-node/src/burnchains/bitcoin_regtest_controller.rs",
    "stacks-node/src/burnchains/mod.rs",
    "stacks-node/src/burnchains/rpc/bitcoin_rpc_client/mod.rs",
    "stacks-node/src/burnchains/rpc/mod.rs",
    "stacks-node/src/burnchains/rpc/rpc_transport/mod.rs",
    "stacks-node/src/event_dispatcher.rs",
    "stacks-node/src/event_dispatcher/db.rs",
    "stacks-node/src/event_dispatcher/payloads.rs",
    "stacks-node/src/event_dispatcher/stacker_db.rs",
    "stacks-node/src/event_dispatcher/worker.rs",
    "stacks-node/src/globals.rs",
    "stacks-node/src/keychain.rs",
    "stacks-node/src/main.rs",
    "stacks-node/src/monitoring/mod.rs",
    "stacks-node/src/monitoring/prometheus.rs",
    "stacks-node/src/nakamoto_node.rs",
    "stacks-node/src/nakamoto_node/miner.rs",
    "stacks-node/src/nakamoto_node/miner_db.rs",
    "stacks-node/src/nakamoto_node/peer.rs",
    "stacks-node/src/nakamoto_node/relayer.rs",
    "stacks-node/src/nakamoto_node/signer_coordinator.rs",
    "stacks-node/src/nakamoto_node/stackerdb_listener.rs",
    "stacks-node/src/neon_node.rs",
    "stacks-node/src/node.rs",
    "stacks-node/src/operations.rs",
    "stacks-node/src/run_loop/boot_nakamoto.rs",
    "stacks-node/src/run_loop/helium.rs",
    "stacks-node/src/run_loop/mod.rs",
    "stacks-node/src/run_loop/nakamoto.rs",
    "stacks-node/src/run_loop/neon.rs",
    "stacks-node/src/syncctl.rs",
    "stacks-node/src/tenure.rs",

    # -- stackslib: consensus, chainstate, the Clarity VM host, burn ops and the P2P/RPC network ----
    "stackslib/src/burnchains/bitcoin/address.rs",
    "stackslib/src/burnchains/bitcoin/bits.rs",
    "stackslib/src/burnchains/bitcoin/blocks.rs",
    "stackslib/src/burnchains/bitcoin/indexer.rs",
    "stackslib/src/burnchains/bitcoin/keys.rs",
    "stackslib/src/burnchains/bitcoin/messages.rs",
    "stackslib/src/burnchains/bitcoin/mod.rs",
    "stackslib/src/burnchains/bitcoin/network.rs",
    "stackslib/src/burnchains/bitcoin/spv.rs",
    "stackslib/src/burnchains/burnchain.rs",
    "stackslib/src/burnchains/db.rs",
    "stackslib/src/burnchains/indexer.rs",
    "stackslib/src/burnchains/mod.rs",
    "stackslib/src/chainstate/burn/atc.rs",
    "stackslib/src/chainstate/burn/db/mod.rs",
    "stackslib/src/chainstate/burn/db/processing.rs",
    "stackslib/src/chainstate/burn/db/sortdb.rs",
    "stackslib/src/chainstate/burn/distribution.rs",
    "stackslib/src/chainstate/burn/mod.rs",
    "stackslib/src/chainstate/burn/operations/delegate_stx.rs",
    "stackslib/src/chainstate/burn/operations/leader_block_commit.rs",
    "stackslib/src/chainstate/burn/operations/leader_key_register.rs",
    "stackslib/src/chainstate/burn/operations/mod.rs",
    "stackslib/src/chainstate/burn/operations/stack_stx.rs",
    "stackslib/src/chainstate/burn/operations/transfer_stx.rs",
    "stackslib/src/chainstate/burn/operations/vote_for_aggregate_key.rs",
    "stackslib/src/chainstate/burn/sortition.rs",
    "stackslib/src/chainstate/coordinator/comm.rs",
    "stackslib/src/chainstate/coordinator/mod.rs",
    "stackslib/src/chainstate/mod.rs",
    "stackslib/src/chainstate/nakamoto/coordinator/mod.rs",
    "stackslib/src/chainstate/nakamoto/keys.rs",
    "stackslib/src/chainstate/nakamoto/miner.rs",
    "stackslib/src/chainstate/nakamoto/mod.rs",
    "stackslib/src/chainstate/nakamoto/shadow.rs",
    "stackslib/src/chainstate/nakamoto/signer_set.rs",
    "stackslib/src/chainstate/nakamoto/staging_blocks.rs",
    "stackslib/src/chainstate/nakamoto/tenure.rs",
    "stackslib/src/chainstate/stacks/address.rs",
    "stackslib/src/chainstate/stacks/auth.rs",
    "stackslib/src/chainstate/stacks/block.rs",
    "stackslib/src/chainstate/stacks/boot/bns.clar",
    "stackslib/src/chainstate/stacks/boot/contract_tests.rs",
    "stackslib/src/chainstate/stacks/boot/cost-voting.clar",
    "stackslib/src/chainstate/stacks/boot/costs-2.clar",
    "stackslib/src/chainstate/stacks/boot/costs-3.clar",
    "stackslib/src/chainstate/stacks/boot/costs-4.clar",
    "stackslib/src/chainstate/stacks/boot/costs.clar",
    "stackslib/src/chainstate/stacks/boot/docs.rs",
    "stackslib/src/chainstate/stacks/boot/genesis.clar",
    "stackslib/src/chainstate/stacks/boot/lockup.clar",
    "stackslib/src/chainstate/stacks/boot/mod.rs",
    "stackslib/src/chainstate/stacks/boot/pox-2.clar",
    "stackslib/src/chainstate/stacks/boot/pox-3.clar",
    "stackslib/src/chainstate/stacks/boot/pox-4.clar",
    "stackslib/src/chainstate/stacks/boot/pox-5.clar",
    "stackslib/src/chainstate/stacks/boot/pox-mainnet.clar",
    "stackslib/src/chainstate/stacks/boot/pox.clar",
    "stackslib/src/chainstate/stacks/boot/pox_2_tests.rs",
    "stackslib/src/chainstate/stacks/boot/pox_3_tests.rs",
    "stackslib/src/chainstate/stacks/boot/pox_4_tests.rs",
    "stackslib/src/chainstate/stacks/boot/signers-0-xxx.clar",
    "stackslib/src/chainstate/stacks/boot/signers-1-xxx.clar",
    "stackslib/src/chainstate/stacks/boot/signers-voting.clar",
    "stackslib/src/chainstate/stacks/boot/signers.clar",
    "stackslib/src/chainstate/stacks/boot/signers_tests.rs",
    "stackslib/src/chainstate/stacks/boot/sip-031.clar",
    "stackslib/src/chainstate/stacks/db/accounts.rs",
    "stackslib/src/chainstate/stacks/db/blocks.rs",
    "stackslib/src/chainstate/stacks/db/contracts.rs",
    "stackslib/src/chainstate/stacks/db/headers.rs",
    "stackslib/src/chainstate/stacks/db/mod.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/blocks.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/burnchain.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/clarity.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/common.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/fork_storage.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/index.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/mod.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/sortition.rs",
    "stackslib/src/chainstate/stacks/db/snapshot/spv.rs",
    "stackslib/src/chainstate/stacks/db/transactions.rs",
    "stackslib/src/chainstate/stacks/db/unconfirmed.rs",
    "stackslib/src/chainstate/stacks/events.rs",
    "stackslib/src/chainstate/stacks/index/bits.rs",
    "stackslib/src/chainstate/stacks/index/blob_layout.rs",
    "stackslib/src/chainstate/stacks/index/cache.rs",
    "stackslib/src/chainstate/stacks/index/file.rs",
    "stackslib/src/chainstate/stacks/index/marf.rs",
    "stackslib/src/chainstate/stacks/index/mod.rs",
    "stackslib/src/chainstate/stacks/index/node.rs",
    "stackslib/src/chainstate/stacks/index/profile.rs",
    "stackslib/src/chainstate/stacks/index/proofs.rs",
    "stackslib/src/chainstate/stacks/index/squash.rs",
    "stackslib/src/chainstate/stacks/index/squash/node_store.rs",
    "stackslib/src/chainstate/stacks/index/squash/stream.rs",
    "stackslib/src/chainstate/stacks/index/storage.rs",
    "stackslib/src/chainstate/stacks/index/trie.rs",
    "stackslib/src/chainstate/stacks/index/trie_sql.rs",
    "stackslib/src/chainstate/stacks/miner.rs",
    "stackslib/src/chainstate/stacks/mod.rs",
    "stackslib/src/chainstate/stacks/sbtc.rs",
    "stackslib/src/chainstate/stacks/transaction.rs",
    "stackslib/src/clarity_vm/clarity.rs",
    "stackslib/src/clarity_vm/database/ephemeral.rs",
    "stackslib/src/clarity_vm/database/marf.rs",
    "stackslib/src/clarity_vm/database/mod.rs",
    "stackslib/src/clarity_vm/mod.rs",
    "stackslib/src/clarity_vm/special.rs",
    "stackslib/src/config/chain_data.rs",
    "stackslib/src/config/mod.rs",
    "stackslib/src/core/mempool.rs",
    "stackslib/src/core/mod.rs",
    "stackslib/src/core/nonce_cache.rs",
    "stackslib/src/cost_estimates/fee_medians.rs",
    "stackslib/src/cost_estimates/fee_rate_fuzzer.rs",
    "stackslib/src/cost_estimates/fee_scalar.rs",
    "stackslib/src/cost_estimates/metrics.rs",
    "stackslib/src/cost_estimates/mod.rs",
    "stackslib/src/cost_estimates/pessimistic.rs",
    "stackslib/src/deps/mod.rs",
    "stackslib/src/lib.rs",
    "stackslib/src/monitoring/mod.rs",
    "stackslib/src/monitoring/prometheus.rs",
    "stackslib/src/net/api/blockreplay.rs",
    "stackslib/src/net/api/blocksimulate.rs",
    "stackslib/src/net/api/callreadonly.rs",
    "stackslib/src/net/api/fastcallreadonly.rs",
    "stackslib/src/net/api/get_tenure_tip_meta.rs",
    "stackslib/src/net/api/get_tenures_fork_info.rs",
    "stackslib/src/net/api/getaccount.rs",
    "stackslib/src/net/api/getattachment.rs",
    "stackslib/src/net/api/getattachmentsinv.rs",
    "stackslib/src/net/api/getblock.rs",
    "stackslib/src/net/api/getblock_v3.rs",
    "stackslib/src/net/api/getblockbyheight.rs",
    "stackslib/src/net/api/getclaritymarfvalue.rs",
    "stackslib/src/net/api/getclaritymetadata.rs",
    "stackslib/src/net/api/getconstantval.rs",
    "stackslib/src/net/api/getcontractabi.rs",
    "stackslib/src/net/api/getcontractsrc.rs",
    "stackslib/src/net/api/getdatavar.rs",
    "stackslib/src/net/api/getheaders.rs",
    "stackslib/src/net/api/gethealth.rs",
    "stackslib/src/net/api/getinfo.rs",
    "stackslib/src/net/api/getistraitimplemented.rs",
    "stackslib/src/net/api/getmapentry.rs",
    "stackslib/src/net/api/getmicroblocks_confirmed.rs",
    "stackslib/src/net/api/getmicroblocks_indexed.rs",
    "stackslib/src/net/api/getmicroblocks_unconfirmed.rs",
    "stackslib/src/net/api/getneighbors.rs",
    "stackslib/src/net/api/getpoxinfo.rs",
    "stackslib/src/net/api/getsigner.rs",
    "stackslib/src/net/api/getsortition.rs",
    "stackslib/src/net/api/getstackerdbchunk.rs",
    "stackslib/src/net/api/getstackerdbmetadata.rs",
    "stackslib/src/net/api/getstackers.rs",
    "stackslib/src/net/api/getstxtransfercost.rs",
    "stackslib/src/net/api/gettenure.rs",
    "stackslib/src/net/api/gettenureblocks.rs",
    "stackslib/src/net/api/gettenureblocksbyhash.rs",
    "stackslib/src/net/api/gettenureblocksbyheight.rs",
    "stackslib/src/net/api/gettenureinfo.rs",
    "stackslib/src/net/api/gettenuretip.rs",
    "stackslib/src/net/api/gettransaction.rs",
    "stackslib/src/net/api/gettransaction_unconfirmed.rs",
    "stackslib/src/net/api/liststackerdbreplicas.rs",
    "stackslib/src/net/api/mod.rs",
    "stackslib/src/net/api/postblock.rs",
    "stackslib/src/net/api/postblock_proposal.rs",
    "stackslib/src/net/api/postblock_v3.rs",
    "stackslib/src/net/api/postfeerate.rs",
    "stackslib/src/net/api/postmempoolquery.rs",
    "stackslib/src/net/api/postmicroblock.rs",
    "stackslib/src/net/api/poststackerdbchunk.rs",
    "stackslib/src/net/api/posttransaction.rs",
    "stackslib/src/net/api/read_only/mod.rs",
    "stackslib/src/net/api/read_only/parse.rs",
    "stackslib/src/net/api/txsimulate.rs",
    "stackslib/src/net/asn.rs",
    "stackslib/src/net/atlas/db.rs",
    "stackslib/src/net/atlas/download.rs",
    "stackslib/src/net/atlas/mod.rs",
    "stackslib/src/net/chat.rs",
    "stackslib/src/net/codec.rs",
    "stackslib/src/net/connection.rs",
    "stackslib/src/net/db.rs",
    "stackslib/src/net/dns.rs",
    "stackslib/src/net/download/epoch2x.rs",
    "stackslib/src/net/download/mod.rs",
    "stackslib/src/net/download/nakamoto/download_state_machine.rs",
    "stackslib/src/net/download/nakamoto/mod.rs",
    "stackslib/src/net/download/nakamoto/tenure.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader_set.rs",
    "stackslib/src/net/download/nakamoto/tenure_downloader_unconfirmed.rs",
    "stackslib/src/net/http/common.rs",
    "stackslib/src/net/http/error.rs",
    "stackslib/src/net/http/mod.rs",
    "stackslib/src/net/http/request.rs",
    "stackslib/src/net/http/response.rs",
    "stackslib/src/net/http/stream.rs",
    "stackslib/src/net/httpcore.rs",
    "stackslib/src/net/inv/epoch2x.rs",
    "stackslib/src/net/inv/mod.rs",
    "stackslib/src/net/inv/nakamoto.rs",
    "stackslib/src/net/mempool/mod.rs",
    "stackslib/src/net/mod.rs",
    "stackslib/src/net/neighbors/comms.rs",
    "stackslib/src/net/neighbors/db.rs",
    "stackslib/src/net/neighbors/mod.rs",
    "stackslib/src/net/neighbors/neighbor.rs",
    "stackslib/src/net/neighbors/rpc.rs",
    "stackslib/src/net/neighbors/walk.rs",
    "stackslib/src/net/p2p.rs",
    "stackslib/src/net/poll.rs",
    "stackslib/src/net/prune.rs",
    "stackslib/src/net/relay.rs",
    "stackslib/src/net/rpc.rs",
    "stackslib/src/net/server.rs",
    "stackslib/src/net/stackerdb/config.rs",
    "stackslib/src/net/stackerdb/db.rs",
    "stackslib/src/net/stackerdb/mod.rs",
    "stackslib/src/net/stackerdb/sync.rs",
    "stackslib/src/net/unsolicited.rs",
    "stackslib/src/util_lib/bloom.rs",
    "stackslib/src/util_lib/boot.rs",
    "stackslib/src/util_lib/db.rs",
    "stackslib/src/util_lib/mod.rs",
    "stackslib/src/util_lib/signed_structured_data.rs",
    "stackslib/src/util_lib/strings.rs",

    # =================================================================================
    # NOT AUDITED (excluded from every variant): tests, mocks and *test* files; fuzz and
    # bench harnesses; test_util and the hooks/testing render helpers; docs/ and README;
    # config, *.toml and CHANGELOG; generated tables (stx-genesis, genesis_data.rs) and
    # build.rs; vendored third-party code under deps_common/ (bitcoin, httparse, bech32,
    # ctrlc); the contrib/ tools and stacks-profiler; sample/ example contracts; and the
    # *-testnet / *.tests.clar network- and test-only contract bodies. A defect in any of
    # these is only in scope when it is reachable from the audited code above.
    # =================================================================================
]


target_scopes = [
    "Critical. THE WINNING SORTITION MUST BE A DETERMINISTIC FUNCTION OF BURN WEIGHT. `sortition.rs` and `distribution.rs` pick the winning `LeaderBlockCommitOp` from the burn distribution and the VRF, with `atc.rs` adjusting the target; `leader_block_commit.rs` `parse_from_tx` / `check` decides which commits are even eligible. Show an unprivileged burnchain participant who is not the highest-weight committer winning the sortition, two nodes selecting different winners from the same burn block, or a commit accepted whose parent/key/burn-fee fields `check` should reject: a modulus or tie-break that depends on HashMap or float ordering, a burn-fee sum that overflows, an ATC adjustment applied inconsistently, a commit referencing a non-existent leader key accepted. Identity: the block-commit the network treats as the sortition winner == the unique commit the deterministic burn-weight-and-VRF function selects on every node.",

    "Critical. A BLOCK IS CANONICAL ONLY IF ITS SIGNER SIGNATURES REACH THE WEIGHT THRESHOLD. `verify_signer_signatures`, `signer_signature_hash`, `check_miner_signature`, `record_block_signers` and `get_signers_weights` (nakamoto/mod.rs, signer_set.rs) accept a block only when signatures from the cycle's reward set exceed the weight threshold. Show a block accepted with insufficient or wrong-set signatures: a `signer_signature_hash` that omits a field the node acts on so a signature over one block validates another, a duplicate signer signature counted twice toward weight, a signature from the previous cycle's set accepted at a cycle boundary, a weight sum that overflows or rounds up, a bitvector/`check_pox_bitvector` mismatch that admits a signer not in the set. Identity: the summed weight of distinct valid signer signatures on an accepted block == a value >= the threshold, drawn from exactly the reward set for that block's cycle.",

    "Critical. THE STATE ROOT COMMITTED MUST EQUAL THE STATE EVERY NODE COMPUTES. The MARF (`marf.rs`, `trie.rs`, `node.rs`, `bits.rs`, `storage.rs`) commits a state root into each block header; every node re-executes the block and must reproduce it, and `proofs.rs` `verify_proof` lets light clients trust it. Show an input where two honest nodes compute different roots for the same block, or a Merkle proof that verifies for a key/value pair not in the committed trie: a node hashing that depends on serialization order, a trie path collision from an ambiguous key encoding, a cursor or back-pointer that reads a stale node across a fork in `storage.rs`, a proof whose shunt/segment path validates a value never written. Identity: the state root in an accepted block header == the root every node's MARF produces after applying the block, and every verifiable proof == a (key, value) actually committed under that root.",

    "Critical. TENURE CHANGE MUST DESCEND FROM EXACTLY ONE PARENT TENURE. `check_tenure_tx`, `validate_nakamoto_tenure_snapshot`, `get_ongoing_tenure`, `get_block_found_tenure`, `has_processed_nakamoto_tenure` and `get_nakamoto_parent_tenure_id_consensus_hash` (nakamoto/mod.rs, tenure.rs) bind each block to a tenure and each tenure to a parent. Show a block that starts a tenure not authorised by its sortition, extends a tenure past its allowed length, reuses a consensus hash across two tenures, or forks a tenure so two branches both claim the same coinbase height: a `TenureChangeCause` accepted in the wrong context, a parent tenure id that resolves to a block on a sibling fork, an `is_new_tenure` check that passes for a replayed tenure-change payload. Identity: every accepted block's tenure == the tenure the winning sortition authorised, descending from exactly one processed parent tenure.",

    "Critical. THE COINBASE PAYS ONCE, TO THE MINER WHO WON. `check_normal_coinbase_tx`, `make_scheduled_miner_reward` (tenure.rs), `insert_miner_payment_schedule`, `find_mature_miner_rewards`, `get_matured_miner_payment`, `insert_matured_parent_miner_reward` / `insert_matured_child_miner_reward` and `MinerReward::try_add_parent` (accounts.rs) schedule and mature the block reward plus fees after the maturity window. Show a miner reward paid twice, paid to the wrong recipient, or crediting more STX than coinbase-plus-fees: a maturation that double-counts across a fork so both branches pay, a `streamed_tx_fees_confirmed` versus `streamed_tx_fees_produced` split that over-credits, a parent/child reward consolidation that adds a reward twice, a recipient principal taken from an unauthenticated coinbase field. Identity: STX credited as a block reward for a tenure == the coinbase plus confirmed fees for that tenure, paid once, to the sortition winner's specified recipient.",

    "Critical. STAGING AND FORK CHOICE MUST YIELD ONE CANONICAL TIP. `staging_blocks.rs`, the coordinator (`coordinator/mod.rs`) and `sortdb.rs` decide which staged block becomes canonical and when a reorg happens; `shadow.rs` handles shadow blocks. Show two nodes disagreeing on the canonical tip after the same burn and Stacks blocks, a staged block accepted that builds on an invalid or unavailable parent, a reorg that un-matures an already-paid reward without reversing the payment, or a shadow block promoted into the canonical chain: an ordering that depends on arrival time, a `NakamotoBlock` accepted whose burnchain view (`common_validate_against_burnchain`, `validate_normal_against_burnchain`) differs between nodes. Identity: the canonical chain tip each node selects from the same burnchain and block set == the same block on every node.",

    "Critical. POISON-MICROBLOCK MUST PAY THE REPORTER FROM THE CHEATER, ONCE. `handle_poison_microblock`, `from_poison_microblock`, `get_poison_microblock_report` (transactions.rs, accounts.rs) let anyone report a miner who signed two microblocks at the same sequence, slashing the miner and paying the reporter. Show an unprivileged reporter slashing a miner who did not equivocate (two headers that are not actually a valid double-sign), collecting the reward twice for one offense, reporting against the wrong miner identity, or a report accepted whose two `StacksMicroblockHeader`s do not both verify under the miner's key. Identity: a poison-microblock reward paid == exactly one valid, previously-unreported double-signature by the slashed miner, verified under that miner's public key.",

    "High. VRF SEED AND LEADER KEY MUST BIND EACH BLOCK TO ITS COMMITTED RANDOMNESS. `validate_vrf_seed`, `check_block_commit_vrf_seed` (nakamoto/mod.rs) and `leader_key_register.rs` bind a block's VRF proof to the seed committed on the burnchain. Show a block whose VRF proof does not correspond to the committed seed and leader key yet is accepted, a seed reused across tenures, or a leader key consumed by two commits: a proof verified against the wrong public key, a seed derived from a mutable field, a key-register op accepted without binding to the committer. Identity: the VRF proof in an accepted block == a proof under the leader key and seed the winning block-commit committed on the burnchain.",

    "High. STATIC VALIDATION MUST REJECT THE SAME BLOCKS ON EVERY NODE. `validate_header_static`, `validate_transactions_static`, `validate_nakamoto_block_static`, `validate_problematic_txs` (nakamoto/mod.rs) run before state execution and must be a pure function of the block bytes and epoch. Show a block that passes static validation on one node and fails on another, or a problematic-transaction classification that depends on node-local state or ordering, so a block is gossiped and half-accepted: a size or count limit compared inconsistently, a transaction epoch-support check that reads mutable config, a `validate_problematic_txs` that flags based on a wall-clock or feature flag. Identity: the static validity verdict for a block == the same verdict on every node at the same epoch.",

    "Critical. THE MISSING INVARIANT - what nobody built. Nothing asserts that across a reorg the sum of matured miner rewards paid on the canonical chain equals coinbase-plus-fees for exactly the canonical tenures; nothing proves the signer weight threshold is recomputed from the same reward set every node derived; the MARF trusts that no two distinct keys share a trie path; sortition selection is assumed independent of map/float iteration order; a tenure-change is assumed to descend from one parent even across shadow blocks. Identify the FIRST place one of these unstated consensus assumptions is violated by input an unprivileged participant can influence (a crafted block-commit, a submitted Nakamoto block, a poison report, a fork they extend), prove it with a Rust integration test on a booted chainstate that drives two nodes or two forks and asserts canonical tip, state root, or total reward before and after, and show that once they diverge the network splits or pays a reward that cannot be reversed.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate Nakamoto-consensus and chainstate-integrity audit questions for one
    stacks-core target.

    ```
    target_file format:
    "'File Name: stackslib/src/chainstate/nakamoto/mod.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate blockchain-consensus security audit questions for this exact stacks-core target:

    {target_file}

    Project focus:
    stacks-core reaches Nakamoto consensus on top of Bitcoin. A Stacks block is accepted only
    if it descends from the winning Bitcoin sortition, carries a valid tenure change, coinbase
    and VRF proof, is signed past the weight threshold by the cycle's reward set, and produces
    the state root every node recomputes in its MARF. Attacker-influenced input arrives as
    burnchain block-commits and leader keys, submitted Nakamoto blocks and signatures,
    poison-microblock reports, and forks the attacker extends. The node decides (a) the one
    canonical tip; (b) whether the committed state root equals the state every node computes;
    (c) whether each block reward is paid exactly once to the miner who won. Anything that
    makes two honest nodes disagree, commits a state root not reproducible, or pays a reward
    twice or to the wrong party, is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust and Clarity symbols (function, struct, enum variant, constant, trait,
      define-* name) as they appear in the file.
    * EVERY question must close on an equality that must hold across block acceptance. State
      it explicitly. Narrative questions with no stated equality are rejected.
    * Attacker is unprivileged only: any participant who can broadcast Bitcoin transactions
      (block-commits, leader keys, burn ops) with their own BTC, submit Nakamoto blocks and
      microblocks over P2P/RPC, file poison reports, and extend forks. They mine and stack
      only with their own resources.
    * Attacker is NOT a majority of signers, not the whole reward set, not a node operator or
      admin, and holds no other signer's or miner's key. No malicious honest-node internal
      state; no compromised dependency; no social engineering. A minority stake or a single
      miner slot IS in scope.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - The Clarity interpreter's asset authority, pox-5 economics, transaction auth, and the
        P2P/RPC network stack are other variants and OUT OF SCOPE here, as are epoch2x/neon
        pre-Nakamoto machinery, README, tests, benches and config.
      - Pure denial of service, gas griefing, block stuffing, unbounded loops and memory
        hygiene are OUT OF SCOPE unless they cause a chain split or reward loss (name it).
      - 51% / majority-stake / majority-signer attacks, economic and Sybil attacks, and
        Bitcoin-consensus defects with no path through this repo are OUT OF SCOPE; a weakness
        here that a minority can trigger is fully IN scope.
      - Also excluded: leaked keys, privileged accounts, centralization risk, best-practice
        notes, feature requests, price assumptions, and theoretical findings.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: a chain split or deep fork; a state root not reproducible across nodes
      (consensus failure); a block accepted that should be invalid or a valid block rejected
      network-wide; block-reward theft, double-payment or unclaimed-reward loss; permanent
      freezing via an irreversible reorg.
      High: a minority-triggerable divergence in sortition, VRF or static validation; a
      poison-microblock or reward mis-payment bounded to fees; temporary tip disagreement.
    * Every question must be a concrete real-world scenario an unprivileged participant can
      execute with their own BTC, their own blocks, and a minority position.
    * A rejection is a finding only when a valid block is permanently rejected network-wide
      or an invalid one accepted - say which.
    * Generate 20 to 40 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable with a Rust integration test on a booted chainstate or
      a two-node/two-fork harness locally. Never propose testing on mainnet or a public
      testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are:
      sortition winner and burn-weight function, signer weight and threshold, committed root
      and recomputed root, tenure and its authorising sortition, reward paid and reward
      earned, verdict on node A and node B.

    Known dead ends - do NOT generate questions about these:
    * Anything needing a majority of signers/stake, a node operator, or another miner's key.
    * A Bitcoin-consensus or dependency bug with no reachable path through this repo.
    * Pure DoS or resource exhaustion with no chain split or reward loss.
    * Findings only reproducible through tests, tooling or pre-Nakamoto epoch2x code.

    Core equalities (each question must close on one):
    * SORTITION: the winner the network accepts == the deterministic burn-weight/VRF winner
      on every node.
    * SIGNING: distinct valid signer weight on an accepted block >= threshold, from that
      cycle's reward set.
    * STATE ROOT: committed root == root every node recomputes; every valid proof == a
      committed (key, value).
    * TENURE/REWARD: each block's tenure == its authorising sortition's; each reward paid
      once == coinbase+fees earned.
    * DETERMINISM: sortition, VRF and static-validation verdicts on node A == on node B.

    Each question must include:
    1. target function, struct or define-* name;
    2. attacker action (a concrete block-commit, block, report or fork with the fields that
       matter);
    3. preconditions (burn state, cycle, reward set, tip);
    4. call sequence through validation, sortition, the MARF and reward maturation;
    5. the equality that breaks, written explicitly;
    6. scoped impact and which nodes or funds are exposed;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: function_or_struct] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, breaking the equality EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: Rust integration test PARAMETERS asserting SORTITION, SIGNING, STATE_ROOT, TENURE_REWARD, or DETERMINISM.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a Nakamoto-consensus exploit-validation prompt for stacks-core.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any participant who can broadcast Bitcoin block-commits, leader keys and burn ops with their own BTC, submit Nakamoto blocks and microblocks, file poison reports and extend forks, holding at most a minority stake or a single miner slot. They are not a majority of signers, not a node operator or admin, and hold no other signer's or miner's key.
- Reject majority-stake / majority-signer / 51% / Sybil / economic attacks, malicious honest-node internal state, compromised dependencies, social engineering, and any path requiring a privileged role.
- OUT OF SCOPE, reject on sight: Clarity interpreter asset authority, pox-5 economics, transaction auth, P2P/RPC internals, epoch2x/neon machinery; README, tests, benches, config; pure denial of service, gas griefing, block stuffing, unbounded loops and memory hygiene unless they cause a chain split or reward loss; Bitcoin-consensus defects with no path through this repo; price assumptions; best-practice notes; theoretical findings.
- The impact must be one of: Critical - a chain split or deep fork, a non-reproducible state root, an invalid block accepted or a valid block rejected network-wide, block-reward theft/double-payment/loss, permanent freezing via irreversible reorg; High - a minority-triggerable sortition/VRF/static-validation divergence, a poison or reward mis-payment bounded to fees, temporary tip disagreement.
- Focus on real impact: two honest nodes disagreeing, a state root no node can reproduce, or a reward paid twice or to the wrong party.

## Validate
- Write the equality the question claims is broken between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's input and record every read and write of the burn distribution, sortition winner, signer weight and reward set, tenure ids, VRF seed, the MARF root and nodes, and the miner-reward schedule.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, `validate_*_static`, `common_validate_against_burnchain`, the MARF hashing, or the maturation window already prevents the divergence.
- State what the attacker gains and whether it is repeatable, and whether it needs only a minority position.
- Require exact file/function support and a reproducible Rust integration test on a local chainstate or two-node/two-fork harness.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken equality, the code path, root cause, the attacker's exact input, exploit flow, and why existing guards fail]

### Impact Explanation
[The split, non-reproducible root, wrongful accept/reject, or reward loss, which nodes/funds, repeatability, matching severity category]

### Likelihood Explanation
[Preconditions, burn/cycle/tip state required, attacker BTC and stake cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust integration test plan with the exact assertions on both sides of the equality]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for stacks-core consensus claims.
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
- Reject anything requiring a majority of signers/stake, a 51%/Sybil/economic attack, a node operator or admin, another signer's or miner's key, malicious honest-node internal state, a compromised dependency, or social engineering. A minority-triggerable path IS valid.
- OUT OF SCOPE, reject on sight: Clarity interpreter asset authority, pox-5 economics, transaction auth, P2P/RPC internals, epoch2x/neon machinery; README, tests, benches, config; pure denial of service, gas griefing, block stuffing, unbounded loops and memory hygiene unless they cause a chain split or reward loss; Bitcoin-consensus defects with no path through this repo; price assumptions; centralization risk; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - a chain split or deep fork, a non-reproducible state root, an invalid block accepted or a valid block rejected network-wide, block-reward theft/double-payment/loss, permanent freezing via irreversible reorg; High - a minority-triggerable sortition/VRF/static-validation divergence, a poison or reward mis-payment bounded to fees, temporary tip disagreement.
- Reject claims where the divergence needs a majority or resolves automatically with no impact.
- Reject if the bug was already fixed, publicly disclosed, or covered by a known-issues list.
- A valid report must be triggerable by an unprivileged participant with a minority position against the current code.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function/struct/define-*, and line references.
2. The equality written explicitly, with both sides shown before and after.
3. Clear root cause: which sortition, signing, state-root, tenure, VRF, static-validation or reward-maturation gap causes it.
4. Reachable exploit path: preconditions -> attacker input -> validation, sortition, MARF and reward sequence -> observed divergence.
5. `check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, the static validators, `common_validate_against_burnchain`, the MARF hashing and the maturation window reviewed and shown insufficient.
6. Impact stated concretely: which nodes disagree or which funds move, and whether it needs only a minority position.
7. Reproducible proof: Rust integration test on a local chainstate or two-node/two-fork harness with the asserted values.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can a minority participant trigger it with no privileged role and no other party's key?
- Is the flaw in this repo's consensus/MARF code, not in a dependency or Bitcoin itself?
- Does the network split, fail to reproduce a root, or mispay a reward, and can it be repeated?
- Would an Immunefi triager accept the exploit path under the Blockchain/DLT severity system?
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
[The split, non-reproducible root, wrongful accept/reject or reward loss, affected nodes/funds, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, state required, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Rust integration test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for stacks-core consensus.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repo context only (nakamoto/**, burn sortition/distribution/operations, coordinator, stacks block/db/accounts, the MARF index, and the signer boot contracts). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only minority-triggerable, unprivileged analogs that break an equality: a sortition winner not matching the burn-weight function, signer weight below threshold or from the wrong set, a committed state root no node reproduces, a tenure not descending from its sortition, a reward paid twice or to the wrong party, or a validation verdict two nodes disagree on.
- OUT OF SCOPE, reject on sight: Clarity asset authority, pox-5 economics, transaction auth, P2P/RPC internals, epoch2x/neon machinery; README, tests, benches, config; pure DoS, gas griefing, block stuffing, unbounded loops and memory hygiene unless they cause a chain split or reward loss; majority/51%/Sybil/economic attacks; Bitcoin-consensus defects with no path through this repo; anything requiring a node operator, admin or another party's key; price assumptions; best-practice notes; theoretical findings.
- The impact must be one of: Critical - a chain split or deep fork, a non-reproducible state root, an invalid block accepted or a valid block rejected network-wide, block-reward theft/double-payment/loss, permanent freezing via irreversible reorg; High - a minority-triggerable sortition/VRF/static-validation divergence, a poison or reward mis-payment bounded to fees, temporary tip disagreement.
- Reject analogs needing a majority or with no reproducible impact.

## Validate
- Map the bug class to the strongest reachable path in this repo and state the equality it would break.
- Evaluate both sides before and after the attacker's input.
- Prove root cause with exact file/function support.
- Accept only concrete chain split, non-reproducible root, wrongful accept/reject, or reward loss.

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
