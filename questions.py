import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
# todo: the path from https://github.com/starkware-libs/sequencer
SOURCE_REPO = "starkware-libs/sequencer"
# todo: the name of the repository
REPO_NAME = "sequencer"
run_number = os.environ.get('GITHUB_RUN_NUMBER') or os.environ.get('CI_PIPELINE_IID', '0')


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
    # Public ingress: the HTTP add_tx endpoint and gateway stateless/stateful validation
    # =================================================================================
    "crates/apollo_http_server/src/http_server.rs",
    "crates/apollo_http_server/src/deprecated_gateway_transaction.rs",
    "crates/apollo_http_server/src/errors.rs",
    "crates/apollo_gateway/src/gateway.rs",
    "crates/apollo_gateway/src/stateless_transaction_validator.rs",
    "crates/apollo_gateway/src/stateful_transaction_validator.rs",
    "crates/apollo_gateway/src/state_reader.rs",
    "crates/apollo_gateway/src/sync_state_reader.rs",
    "crates/apollo_gateway/src/gateway_fixed_block_state_reader.rs",
    "crates/apollo_gateway/src/errors.rs",
    "crates/apollo_transaction_converter/src/transaction_converter.rs",

    # =================================================================================
    # Transaction encoding, fields, hashing and the identity a signature commits to
    # =================================================================================
    "crates/starknet_api/src/rpc_transaction.rs",
    "crates/starknet_api/src/transaction.rs",
    "crates/starknet_api/src/transaction/fields.rs",
    "crates/starknet_api/src/transaction/constants.rs",
    "crates/starknet_api/src/transaction_hash.rs",
    "crates/starknet_api/src/executable_transaction.rs",
    "crates/starknet_api/src/consensus_transaction.rs",
    "crates/starknet_api/src/core.rs",
    "crates/starknet_api/src/hash.rs",
    "crates/starknet_api/src/crypto/utils.rs",
    "crates/starknet_api/src/crypto/patricia_hash.rs",
    "crates/starknet_api/src/serde_utils.rs",
    "crates/starknet_api/src/compression_utils.rs",
    "crates/starknet_api/src/execution_resources.rs",
    "crates/starknet_api/src/versioned_constants_logic.rs",
    "crates/starknet_api/src/state.rs",
    "crates/starknet_api/src/block.rs",

    # =================================================================================
    # Declare pipeline: Sierra -> CASM compilation, class hashing and class storage
    # =================================================================================
    "crates/apollo_compile_to_casm/src/compiler.rs",
    "crates/apollo_compile_to_casm/src/constants.rs",
    "crates/apollo_class_manager/src/class_manager.rs",
    "crates/apollo_class_manager/src/class_storage.rs",
    "crates/starknet_api/src/contract_class.rs",
    "crates/starknet_api/src/contract_class/compiled_class_hash.rs",
    "crates/starknet_api/src/contract_class/structs.rs",
    "crates/starknet_api/src/deprecated_contract_class.rs",
    "crates/blockifier/src/execution/contract_class.rs",
    "crates/blockifier/src/execution/casm_hash_estimation.rs",
    "crates/blockifier/src/state/contract_class_manager.rs",
    "crates/blockifier/src/state/native_class_manager.rs",
    "crates/blockifier/src/state/global_cache.rs",
    "crates/blockifier/src/state/compiled_class_hash_migration.rs",

    # =================================================================================
    # Mempool admission, ordering, replacement and eviction of user transactions
    # =================================================================================
    "crates/apollo_mempool/src/mempool.rs",
    "crates/apollo_mempool/src/transaction_pool.rs",
    "crates/apollo_mempool/src/fee_transaction_queue.rs",
    "crates/apollo_mempool/src/fifo_transaction_queue.rs",
    "crates/apollo_mempool/src/utils.rs",

    # =================================================================================
    # L1 -> L2 messaging: the permissionless L1 entrypoint into L1 handler transactions
    # =================================================================================
    "crates/papyrus_base_layer/src/eth_events.rs",
    "crates/papyrus_base_layer/src/ethereum_base_layer_contract.rs",
    "crates/papyrus_base_layer/src/constants.rs",
    "crates/apollo_l1_provider/src/l1_provider.rs",
    "crates/apollo_l1_provider/src/l1_scraper.rs",
    "crates/apollo_l1_provider/src/transaction_manager.rs",
    "crates/apollo_l1_provider/src/transaction_record.rs",
    "crates/apollo_l1_provider/src/catchupper.rs",
    "crates/blockifier/src/transaction/l1_handler_transaction.rs",

    # =================================================================================
    # Account transaction lifecycle: validate/execute stages, nonces, revert, fee charge
    # =================================================================================
    "crates/blockifier/src/transaction/account_transaction.rs",
    "crates/blockifier/src/transaction/transaction_execution.rs",
    "crates/blockifier/src/transaction/transactions.rs",
    "crates/blockifier/src/transaction/objects.rs",
    "crates/blockifier/src/transaction/errors.rs",
    "crates/blockifier/src/blockifier/stateful_validator.rs",
    "crates/blockifier/src/blockifier/transaction_executor.rs",
    "crates/blockifier/src/blockifier/concurrent_transaction_executor.rs",
    "crates/blockifier/src/blockifier/block.rs",
    "crates/blockifier/src/context.rs",

    # =================================================================================
    # Fee, gas and resource accounting: value conservation for every charged transaction
    # =================================================================================
    "crates/blockifier/src/fee/fee_checks.rs",
    "crates/blockifier/src/fee/fee_utils.rs",
    "crates/blockifier/src/fee/gas_usage.rs",
    "crates/blockifier/src/fee/receipt.rs",
    "crates/blockifier/src/fee/resources.rs",
    "crates/blockifier/src/fee/eth_gas_constants.rs",
    "crates/blockifier/src/bouncer.rs",
    "crates/blockifier/src/blockifier_versioned_constants.rs",
    "crates/apollo_consensus_orchestrator/src/fee_market/mod.rs",

    # =================================================================================
    # Entrypoint dispatch and syscalls: the surface attacker contract code drives directly
    # =================================================================================
    "crates/blockifier/src/execution/entry_point.rs",
    "crates/blockifier/src/execution/entry_point_execution.rs",
    "crates/blockifier/src/execution/deprecated_entry_point_execution.rs",
    "crates/blockifier/src/execution/execution_utils.rs",
    "crates/blockifier/src/execution/call_info.rs",
    "crates/blockifier/src/execution/contract_address.rs",
    "crates/blockifier/src/execution/common_hints.rs",
    "crates/blockifier/src/execution/stack_trace.rs",
    "crates/blockifier/src/execution/syscalls/mod.rs",
    "crates/blockifier/src/execution/syscalls/syscall_base.rs",
    "crates/blockifier/src/execution/syscalls/syscall_executor.rs",
    "crates/blockifier/src/execution/syscalls/hint_processor.rs",
    "crates/blockifier/src/execution/syscalls/common_syscall_logic.rs",
    "crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs",
    "crates/blockifier/src/execution/syscalls/secp.rs",
    "crates/blockifier/src/execution/secp.rs",
    "crates/blockifier/src/execution/deprecated_syscalls/mod.rs",
    "crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs",
    "crates/blockifier/src/execution/deprecated_syscalls/deprecated_syscall_executor.rs",
    "crates/blockifier/src/execution/native/entry_point_execution.rs",
    "crates/blockifier/src/execution/native/syscall_handler.rs",
    "crates/blockifier/src/execution/native/contract_class.rs",
    "crates/blockifier/src/execution/native/utils.rs",
    "crates/blockifier/src/abi/sierra_types.rs",
    "crates/blockifier/src/abi/constants.rs",

    # =================================================================================
    # State reads/writes, aliasing and parallel execution determinism
    # =================================================================================
    "crates/blockifier/src/state/cached_state.rs",
    "crates/blockifier/src/state/state_api.rs",
    "crates/blockifier/src/state/state_reader_and_contract_manager.rs",
    "crates/blockifier/src/state/stateful_compression.rs",
    "crates/blockifier/src/state/utils.rs",
    "crates/blockifier/src/concurrency/versioned_state.rs",
    "crates/blockifier/src/concurrency/versioned_storage.rs",
    "crates/blockifier/src/concurrency/scheduler.rs",
    "crates/blockifier/src/concurrency/worker_logic.rs",
    "crates/blockifier/src/concurrency/worker_pool.rs",
    "crates/blockifier/src/concurrency/fee_utils.rs",

    # =================================================================================
    # Block building and proposal: where a user transaction becomes part of a block
    # =================================================================================
    "crates/apollo_batcher/src/batcher.rs",
    "crates/apollo_batcher/src/block_builder.rs",
    "crates/apollo_batcher/src/transaction_executor.rs",
    "crates/apollo_batcher/src/transaction_provider.rs",
    "crates/apollo_batcher/src/pre_confirmed_block_writer.rs",
    "crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs",
    "crates/apollo_batcher/src/commitment_manager/state_committer.rs",
    "crates/apollo_batcher/src/utils.rs",
    "crates/apollo_consensus_orchestrator/src/build_proposal.rs",
    "crates/apollo_consensus_orchestrator/src/validate_proposal.rs",
    "crates/apollo_consensus_orchestrator/src/cende/central_objects.rs",
    "crates/apollo_committer/src/committer.rs",

    # =================================================================================
    # Block hash and commitments: the values honest nodes must agree on bit for bit
    # =================================================================================
    "crates/starknet_api/src/block_hash/block_hash_calculator.rs",
    "crates/starknet_api/src/block_hash/receipt_commitment.rs",
    "crates/starknet_api/src/block_hash/event_commitment.rs",
    "crates/starknet_api/src/block_hash/transaction_commitment.rs",
    "crates/starknet_api/src/block_hash/state_diff_hash.rs",

    # =================================================================================
    # State commitment: committer forests and the Patricia trees behind the state root
    # =================================================================================
    "crates/starknet_committer/src/block_committer/commit.rs",
    "crates/starknet_committer/src/block_committer/input.rs",
    "crates/starknet_committer/src/block_committer/state_diff_generator.rs",
    "crates/starknet_committer/src/forest/original_skeleton_forest.rs",
    "crates/starknet_committer/src/forest/updated_skeleton_forest.rs",
    "crates/starknet_committer/src/forest/filled_forest.rs",
    "crates/starknet_committer/src/hash_function/hash.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/leaf/leaf_impl.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/leaf/leaf_serde.rs",
    "crates/starknet_committer/src/db/facts_db/node_serde.rs",
    "crates/starknet_committer/src/db/facts_db/create_facts_tree.rs",
    "crates/starknet_committer/src/db/index_db/leaves.rs",
    "crates/starknet_committer/src/db/trie_traversal.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/original_skeleton_tree/tree.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/original_skeleton_tree/utils.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/updated_skeleton_tree/create_tree_helper.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/updated_skeleton_tree/tree.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/updated_skeleton_tree/hash_function.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/filled_tree/tree.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/node_data/inner_node.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/node_data/leaf.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/traversal.rs",
    "crates/starknet_patricia/src/patricia_merkle_tree/types.rs",

    # =================================================================================
    # Starknet OS: the proved re-execution that must match what the sequencer committed
    # =================================================================================
    "crates/starknet_os/src/runner.rs",
    "crates/starknet_os/src/io/os_input.rs",
    "crates/starknet_os/src/io/os_output.rs",
    "crates/starknet_os/src/hint_processor/execution_helper.rs",
    "crates/starknet_os/src/hint_processor/snos_hint_processor.rs",
    "crates/starknet_os/src/hint_processor/snos_syscall_executor.rs",
    "crates/starknet_os/src/hint_processor/snos_deprecated_syscall_executor.rs",
    "crates/starknet_os/src/hint_processor/state_update_pointers.rs",
    "crates/starknet_os/src/hints/hint_implementation/execution/implementation.rs",
    "crates/starknet_os/src/hints/hint_implementation/execute_transactions/implementation.rs",
    "crates/starknet_os/src/hints/hint_implementation/execute_syscalls.rs",
    "crates/starknet_os/src/hints/hint_implementation/patricia/implementation.rs",
    "crates/starknet_os/src/hints/hint_implementation/patricia/utils.rs",
    "crates/starknet_os/src/hints/hint_implementation/stateless_compression/utils.rs",
    "crates/starknet_os/src/hints/hint_implementation/stateful_compression/implementation.rs",
    "crates/starknet_os/src/hints/hint_implementation/compiled_class/implementation.rs",
    "crates/starknet_os/src/hints/hint_implementation/deprecated_compiled_class/class_hash.rs",
    "crates/starknet_os/src/hints/class_hash/hinted_class_hash.rs",
    "crates/starknet_os/src/hints/hint_implementation/cairo1_revert/implementation.rs",
    "crates/starknet_os/src/hints/hint_implementation/kzg/utils.rs",
    "crates/starknet_os/src/hints/hint_implementation/output.rs",
]


target_scopes = [
    "Critical. A user loses funds or pays the wrong amount because fee accounting is wrong: check_fee_bounds, handle_fee, execute_fee_transfer and assert_actual_fee_in_bounds in blockifier/src/transaction/account_transaction.rs, PostExecutionReport and check_actual_cost_within_bounds in fee/fee_checks.rs, get_fee_by_gas_vector and balance reads in fee/fee_utils.rs, TransactionReceipt::from_account_tx in fee/receipt.rs, or gas_usage.rs data-availability costs let an attacker's transaction charge a victim account more than its declared resource bounds, charge nothing for consumed resources, or transfer the fee to an address other than the sequencer.",
    "Critical. An attacker executes calls or spends state belonging to an account whose keys they do not hold, because the validate/execute boundary is not enforced: validate_entry_point_selector, validate_entrypoint_calldata, run_validate_entry_point, handle_nonce and run_revertible in account_transaction.rs, sender-address and nonce checks in blockifier/src/blockifier/stateful_validator.rs, calculate_contract_address in execution/contract_address.rs, or the meta_tx, library_call, replace_class, deploy and call_contract paths in execution/syscalls/syscall_base.rs and hint_processor.rs run attacker calldata under another account's context or bind a signature to a payload the owner never authorized.",
    "Critical. A declared class executes code that does not match the hash the network committed to, because the declare pipeline breaks the Sierra-to-CASM binding: compile in apollo_compile_to_casm/src/compiler.rs, the compiled_class_hash check in apollo_class_manager/src/class_manager.rs and class_storage.rs, CompiledClassHash computation in starknet_api/src/contract_class/compiled_class_hash.rs, RunnableCompiledClass construction in blockifier/src/execution/contract_class.rs, or the native/VM selection in state/native_class_manager.rs lets an attacker get one class hash to resolve to two different executables.",
    "Critical. Funds are permanently frozen or an L1 deposit is consumed twice, because L1 handler bookkeeping is wrong: parse_event in papyrus_base_layer/src/eth_events.rs, add_events, validate and commit_block in apollo_l1_provider/src/l1_provider.rs, add_tx, validate_tx, consume_tx, commit_txs, request_cancellation and finalize_cancellation in transaction_manager.rs, the staged/consumed state machine in transaction_record.rs, or L1HandlerTransaction fee and nonce handling in blockifier/src/transaction/l1_handler_transaction.rs lets an attacker replay a consumed message, cancel one already included, or make a paid message unconsumable forever.",
    "Critical. The committed state root does not reflect the executed state diff, so balances are silently wrong or the chain can no longer be proved and funds are frozen: commit_block in starknet_committer/src/block_committer/commit.rs, ForestSortedIndices and skeleton construction in forest/original_skeleton_forest.rs and updated_skeleton_forest.rs, leaf encoding in patricia_merkle_tree/leaf/leaf_impl.rs and leaf_serde.rs, edge/binary node hashing in starknet_patricia/src/patricia_merkle_tree/updated_skeleton_tree/hash_function.rs and node_data/inner_node.rs, create_tree_helper.rs path splitting, or alias allocation in blockifier/src/state/stateful_compression.rs produces a root that omits, duplicates or misplaces an attacker-chosen storage key.",
    "High. Honest nodes compute different block hashes or commitments for the same accepted block, splitting the chain: calculate_block_hash in starknet_api/src/block_hash/block_hash_calculator.rs, receipt_commitment.rs, event_commitment.rs, transaction_commitment.rs and state_diff_hash.rs, the diff assembled in apollo_batcher/src/commitment_manager/state_committer.rs, or the central objects in apollo_consensus_orchestrator/src/cende/central_objects.rs serialize attacker-controlled events, l2 gas, revert reasons or state-diff ordering in a way that is not canonical.",
    "High. The same attacker transaction produces different execution results on different honest nodes, causing a chain split: versioned reads and writes in blockifier/src/concurrency/versioned_state.rs and versioned_storage.rs, re-validation and commit ordering in concurrency/scheduler.rs and worker_logic.rs commit_tx/validate, cache reuse in state/cached_state.rs and state/global_cache.rs, gas or error differences between execution/native/entry_point_execution.rs and execution/entry_point_execution.rs, or non-deterministic iteration of state-diff and event collections makes a parallel run disagree with a sequential one.",
    "High. The Starknet OS re-execution disagrees with what the sequencer committed, so no valid proof can be produced and the network stops advancing: execution_helper.rs per-transaction state, snos_syscall_executor.rs and snos_deprecated_syscall_executor.rs syscall replay, state_update_pointers.rs, patricia/utils.rs and patricia/implementation.rs tree reconstruction, stateless_compression/utils.rs and stateful_compression/implementation.rs encoding, cairo1_revert/implementation.rs revert reconstruction, or os_input.rs/os_output.rs field ordering mishandle an attacker-chosen syscall sequence, revert or storage pattern.",
    "High. A single crafted transaction or L1 message permanently stops the network from confirming new transactions, because it panics, deadlocks or wedges a stage it is replayed into after restart: add_tx in apollo_http_server/src/http_server.rs and apollo_gateway/src/gateway.rs, validate in stateless_transaction_validator.rs, add_tx/get_txs/commit_block and try_make_space in apollo_mempool/src/mempool.rs, Bouncer::try_update and within_max_capacity_or_err in blockifier/src/bouncer.rs, build_block in apollo_batcher/src/block_builder.rs, or proposal handling in apollo_consensus_orchestrator/src/build_proposal.rs and validate_proposal.rs, where the same transaction is re-selected every height and every block build fails.",
    "Critical/High blind spot. An ordinary transaction sender, contract deployer, class declarer or L1 message sender abuses an assumption the sequencer never wrote down: a value validated in apollo_gateway against one block state and trusted as still valid when the batcher executes it at a later height, a class, nonce, alias or compiled-class hash re-read after the check that authorized it, a limit enforced for the VM path but not the cairo_native path or for an account transaction but not its l1_handler or meta_tx twin, state carried across transaction, block, chunk, revert, restart or version-boundary lines that was only proven safe inside one of them, or an error path that keeps partial state, charged fees or a written alias - yielding loss or permanent freezing of user funds, honest nodes splitting the chain, or the network permanently unable to confirm new transactions.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one sequencer target.

    ```
    target_file format:
    "'File Name: crates/blockifier/src/fee/fee_checks.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact Apollo Starknet sequencer target:

    {target_file}

    Project focus:
    Apollo is the Starknet sequencer. Focus only on what an ordinary user reaches: submitting invoke, declare and deploy_account transactions to the public HTTP gateway, the contract code and calldata they deploy and call, the Sierra classes they declare, the syscalls their contracts issue, and the L1 handler messages they trigger by calling the Starknet core contract on L1. Downstream of that: mempool admission and ordering, blockifier execution and fee charging, bouncer weights, block building, state commitment in the committer and Patricia trees, block hash and commitments, and Starknet OS re-execution of those blocks.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (function, method, struct, enum variant, trait impl, const) when possible.
    * Attacker is unprivileged only: anyone who funds an account and submits signed transactions of any version, deploys and calls their own contracts, declares their own Sierra classes, or sends an L1 to L2 message by calling the core contract on L1. They sign only for their own accounts.
    * Attacker is NOT a sequencer operator, proposer, validator, staker, prover, node operator, host or DB owner, and does not hold another user's key. Never assume a malicious peer, malicious node, malicious proposer or validator, p2p/gossip/sync/catchup attacker, network-level DoS, leaked key, compromised host, non-default config, or social engineering.
    * Out of scope, never ask about: p2p networking and peer handling, consensus voting and proposer selection, state sync between nodes, monitoring and dashboard endpoints, CLI, logging, deployment and infra, dependencies.
    * Ignore test files, mocks, benchmarks, docs, generated files, and config-only findings.
    * Every question must describe a real transaction, contract call, declared class or L1 message an attacker actually submits through a valid entrypoint. No generic unbounded-allocation, memory-growth, cache-size, or resource-exhaustion speculation; no "what if the input is huge" without a concrete submitted payload and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target loss or permanent freezing of user funds, acting on an account without its keys, a wrong committed state root or block hash, honest nodes splitting the chain, or the network permanently unable to confirm new transactions.
    * Every question must be testable by a `cargo test -p <crate>` unit test, a blockifier transaction-execution test, a committer or OS flow test, or a local integration-test node run.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: a call runs against an account's state only when that account's __validate__ accepted the exact transaction hash that is executed and committed.
    * Value is conserved: fees charged equal resources consumed within the sender's declared resource bounds, are paid to the sequencer once, and no path mints, burns or strands balance.
    * Determinism holds: every honest node executing the same block reaches the same results, fees, events, state diff, state root and block hash, whether run sequentially, concurrently, on the VM or on cairo_native.
    * Provability holds: the Starknet OS re-execution of a committed block reproduces exactly the sequencer's outputs, so every committed block can be proved.
    * Liveness of valid users: no submitted transaction, declared class or L1 message can permanently stop the sequencer from building and committing new blocks.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction, contract call, declared class or L1 message: type, version, fields, calldata);
    3. preconditions (accounts, balance, deployed contracts and classes the attacker owns);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: cargo test unit/execution/committer/OS-flow/integration test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, PROVABILITY, or USER_LIVENESS.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused sequencer exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: anyone who funds an account and submits signed transactions to the public gateway, deploys and calls their own contracts, declares their own Sierra classes, or sends an L1 to L2 message via the core contract. No operator, proposer, validator, staker, prover, node, host, DB, or foreign-key access.
- Reject malicious-operator, malicious-proposer, malicious-peer, malicious-node, p2p/gossip/sync/catchup, network-DoS, leaked-key, host-level, and misconfiguration-only paths.
- Reject 51%-style, sybil and centralization claims, and monitoring, dashboard, CLI, logging, deployment, dependency-only, and test/mock/bench/generated/config-only findings.
- Reject generic unbounded-allocation or resource-growth claims with no concrete submitted payload and no broken invariant.
- This program pays High and Critical only. Focus on real chain impact: direct loss or permanent freezing of user funds, acting on an account without its keys, a committed state root or block hash that does not match executed state, honest nodes splitting the chain, or the network permanently unable to confirm new transactions.

## Validate
- Trace the exact reachable path from the attacker's transaction, contract call, declared class or L1 message into the affected function.
- Check whether gateway stateless and stateful validation, signature and nonce checks, resource-bound and fee checks, compiled-class-hash verification, bouncer limits, or existing error handling already stop it.
- Confirm the path is reachable under current mainnet versioned constants and the active Starknet version.
- Accept only concrete fund loss or freezing, unauthorized account action, wrong committed root or block hash, node divergence, or a lasting inability to produce blocks.
- Require exact file/function support and a reproducible cargo test, blockifier execution test, committer/OS flow test, or integration-test PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker payload, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (direct loss of user funds, permanent freezing of funds, executing transactions from another user's account without their keys, protocol insolvency) or High (unintended chain split between honest nodes, network unable to confirm new transactions, unprovable committed block, corruption of committed state)]

### Likelihood Explanation
[Preconditions, accounts and balance needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo test / execution / committer / OS flow / integration test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for the sequencer.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction sender, contract deployer, class declarer or L1 message sender can reach: gateway validation, transaction hashing and fields, Sierra to CASM compilation and class hashing, mempool admission and ordering, blockifier execution, syscalls, fee and resource accounting, bouncer weights, state reads and aliasing, block building, state commitment and Patricia trees, block hash and commitments, or Starknet OS re-execution.
- Reject malicious-operator, malicious-proposer, malicious-peer, malicious-node, p2p/sync/catchup, network-DoS, leaked-key, staker-only, prover-only, monitoring, CLI, deployment, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium , High and Critical only; no low, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable sequencer path from a single submitted transaction, contract call, declared class or L1 message.
- Prove root cause with exact file/function support.
- Accept only concrete loss or permanent freezing of funds, unauthorized account action, wrong committed root or block hash, honest-node divergence, or a network unable to confirm new transactions.

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


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for sequencer security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- This program pays High and Critical only; reject low, medium, informational, best-practice, and resource-only reports.
- Reject malicious-operator, malicious-proposer, malicious-validator, malicious-peer, malicious-node, p2p/gossip/sync/catchup, network-level DoS, monitoring and dashboard endpoints, CLI, logging, deployment and infra, dependency-only, docs/style, generated-file, and test/mock/bench/config-only issues.
- Reject if the exploit needs sequencer operator, proposer, validator, staker, prover, node, host, database, or privileged access, another user's key, victim social engineering, a non-default config, or anything outside what an unprivileged user can put in a submitted transaction, a contract they deploy, a class they declare, or an L1 to L2 message they send.
- Reject 51%-style majority attacks, sybil and centralization claims, and third-party oracle data being wrong without a manipulation path.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction sender, contract deployer, class declarer or L1 message sender, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - direct theft or loss of user funds, permanent freezing of funds, executing transactions from another user's account without their private keys, or protocol insolvency; High - unintended chain split between honest nodes, the network unable to confirm new transactions, a committed block that cannot be proved, or corruption of committed state, balances or class code.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken authorization, value-conservation, determinism, provability, or user-liveness invariant.
3. Reachable exploit path: preconditions (attacker-owned accounts, balance, deployed contracts, declared classes) -> submitted transaction, contract call, declared class or L1 message -> trigger -> bad result.
4. Existing gateway stateless and stateful validation, signature and nonce checks, resource-bound and fee checks, compiled-class-hash verification, bouncer limits, and error handling reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: cargo test unit PoC, blockifier execution test, committer or OS flow test, or exact steps on a local integration-test node.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary user trigger this by submitting a transaction, deploying or calling a contract, declaring a class, or sending an L1 message, without operator, proposer, validator, prover, host, or foreign-key access?
- Does the code actually behave as claimed under current mainnet versioned constants and the active Starknet version?
- Is the impact caused by this code, not by a malicious operator, peer, or dependency?
- Is the fund loss, unauthorized action, divergence, or halt concrete rather than hypothetical?
- Would a Starknet triager on Immunefi accept the proof-of-concept?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and Starknet bounty category]

## Likelihood Explanation
[Attacker capability, accounts and balance required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or cargo test / execution / committer / OS flow / integration test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
