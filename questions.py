import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the path from https://github.com/near/nearcore
SOURCE_REPO = "near/nearcore"
# todo: the name of the repository
REPO_NAME = "nearcore"
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
    # Transaction and action validation: signatures, nonces, access keys, meta-txs
    # =================================================================================
    "runtime/runtime/src/verifier.rs",
    "runtime/runtime/src/action_validation.rs",
    "runtime/runtime/src/access_keys.rs",
    "core/primitives/src/transaction.rs",
    "core/primitives/src/action/mod.rs",
    "core/primitives/src/action/delegate.rs",
    "core/primitives/src/signable_message.rs",
    "core/primitives/src/receipt.rs",
    "core/primitives/src/errors.rs",
    "core/primitives-core/src/account.rs",
    "core/primitives-core/src/types.rs",
    "core/primitives-core/src/errors.rs",
    "core/primitives-core/src/serialize.rs",
    "core/crypto/src/signature.rs",
    "core/crypto/src/key_conversion.rs",
    "core/crypto/src/hash.rs",
    "chain/chain/src/signature_verification.rs",
    "chain/chain/src/validate.rs",

    # =================================================================================
    # Runtime apply loop: action execution, balance flow, refunds, receipt generation
    # =================================================================================
    "runtime/runtime/src/lib.rs",
    "runtime/runtime/src/actions.rs",
    "runtime/runtime/src/receipt_manager.rs",
    "runtime/runtime/src/function_call.rs",
    "runtime/runtime/src/ext.rs",
    "runtime/runtime/src/config.rs",
    "runtime/runtime/src/conversions.rs",
    "runtime/runtime/src/pipelining.rs",
    "runtime/runtime/src/contract_code.rs",
    "runtime/runtime/src/types.rs",
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/prefetch.rs",
    "runtime/runtime/src/cache_warming.rs",
    "runtime/runtime/src/state_viewer/mod.rs",
    "runtime/runtime/src/state_viewer/errors.rs",
    "core/primitives-core/src/apply.rs",

    # =================================================================================
    # Global contracts, deterministic and universal accounts, state init
    # =================================================================================
    "runtime/runtime/src/global_contracts.rs",
    "runtime/runtime/src/deterministic_account_id.rs",
    "runtime/runtime/src/universal_account_id.rs",
    "core/primitives-core/src/global_contract.rs",
    "core/primitives-core/src/deterministic_account_id.rs",
    "core/primitives-core/src/universal_account_id.rs",
    "core/primitives-core/src/universal_state_init.rs",
    "core/primitives/src/universal_state_init.rs",
    "core/primitives-core/src/code.rs",
    "core/store/src/contract.rs",

    # =================================================================================
    # Gas metering, fee schedule and protocol parameters
    # =================================================================================
    "core/parameters/src/config.rs",
    "core/parameters/src/config_store.rs",
    "core/parameters/src/cost.rs",
    "core/parameters/src/parameter_table.rs",
    "core/parameters/src/parameter.rs",
    "core/parameters/src/view.rs",
    "core/parameters/src/vm.rs",
    "core/primitives-core/src/gas.rs",
    "core/primitives-core/src/config.rs",
    "core/primitives-core/src/version.rs",
    "core/primitives/src/version.rs",
    "core/primitives/src/upgrade_schedule.rs",
    "core/primitives/src/profile_data_v3.rs",

    # =================================================================================
    # WASM: preparation, instrumentation, host functions, gas counter, VM cache
    # =================================================================================
    "runtime/near-vm-runner/src/prepare.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v3.rs",
    "runtime/near-vm-runner/src/prepare/instrument_v3.rs",
    "runtime/near-vm-runner/src/runner.rs",
    "runtime/near-vm-runner/src/cache.rs",
    "runtime/near-vm-runner/src/imports.rs",
    "runtime/near-vm-runner/src/features.rs",
    "runtime/near-vm-runner/src/errors.rs",
    "runtime/near-vm-runner/src/profile.rs",
    "runtime/near-vm-runner/src/utils.rs",
    "runtime/near-vm-runner/src/logic/logic.rs",
    "runtime/near-vm-runner/src/logic/gas_counter.rs",
    "runtime/near-vm-runner/src/logic/vmstate.rs",
    "runtime/near-vm-runner/src/logic/context.rs",
    "runtime/near-vm-runner/src/logic/dependencies.rs",
    "runtime/near-vm-runner/src/logic/recorded_storage_counter.rs",
    "runtime/near-vm-runner/src/logic/alt_bn128.rs",
    "runtime/near-vm-runner/src/logic/bls12381.rs",
    "runtime/near-vm-runner/src/logic/errors.rs",
    "runtime/near-vm-runner/src/logic/types.rs",
    "runtime/near-vm-runner/src/logic/utils.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/mod.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/logic.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/trap_classification.rs",

    # =================================================================================
    # Cross-shard receipt flow: congestion control, bandwidth scheduler, buffers
    # =================================================================================
    "runtime/runtime/src/congestion_control.rs",
    "runtime/runtime/src/bandwidth_scheduler/mod.rs",
    "runtime/runtime/src/bandwidth_scheduler/scheduler.rs",
    "runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs",
    "core/primitives/src/congestion_info.rs",
    "core/primitives/src/bandwidth_scheduler.rs",
    "core/store/src/trie/receipts_column_helper.rs",
    "core/store/src/trie/outgoing_metadata.rs",
    "chain/chain/src/receipt_to_tx.rs",

    # =================================================================================
    # Trie and state storage touched by every user write
    # =================================================================================
    "core/store/src/trie/mod.rs",
    "core/store/src/trie/update.rs",
    "core/store/src/trie/trie_storage.rs",
    "core/store/src/trie/trie_storage_update.rs",
    "core/store/src/trie/trie_recording.rs",
    "core/store/src/trie/raw_node.rs",
    "core/store/src/trie/nibble_slice.rs",
    "core/store/src/trie/iterator.rs",
    "core/store/src/trie/shard_tries.rs",
    "core/store/src/trie/state_parts.rs",
    "core/store/src/trie/config.rs",
    "core/store/src/trie/ops/insert_delete.rs",
    "core/store/src/trie/ops/interface.rs",
    "core/store/src/trie/ops/iter.rs",
    "core/store/src/trie/ops/squash.rs",
    "core/store/src/trie/ops/resharding.rs",
    "core/store/src/trie/mem/memtrie_update.rs",
    "core/store/src/trie/mem/lookup.rs",
    "core/store/src/trie/mem/node/encoding.rs",
    "core/store/src/trie/mem/node/view.rs",
    "core/store/src/trie/mem/flexible_data/encoding.rs",
    "core/store/src/trie/mem/flexible_data/children.rs",
    "core/store/src/trie/mem/flexible_data/extension.rs",
    "core/store/src/trie/mem/flexible_data/value.rs",
    "core/store/src/trie/mem/arena/alloc.rs",
    "core/store/src/trie/mem/freelist.rs",
    "core/store/src/flat/storage.rs",
    "core/store/src/flat/chunk_view.rs",
    "core/store/src/flat/delta.rs",
    "core/store/src/flat/manager.rs",
    "core/store/src/db/refcount.rs",
    "core/store/src/merkle_proof.rs",
    "core/primitives-core/src/trie_key.rs",
    "core/primitives/src/trie_key.rs",
    "core/primitives/src/state_record.rs",
    "core/primitives/src/state.rs",

    # =================================================================================
    # Transaction admission, chunk transaction selection and tx pool
    # =================================================================================
    "chain/pool/src/lib.rs",
    "chain/pool/src/types.rs",
    "chain/client/src/prepare_transactions.rs",
    "chain/client/src/rpc_handler.rs",
    "chain/client/src/pending_transaction_queue.rs",
    "chain/client/src/chunk_producer.rs",
    "chain/chain/src/chain.rs",
    "chain/chain/src/chain_update.rs",
    "chain/chain/src/update_shard.rs",
    "chain/chain/src/sharding.rs",
    "chain/chain/src/types.rs",
    "chain/chunks/src/logic.rs",

    # =================================================================================
    # Stateless validation surface a user transaction can inflate or corrupt
    # =================================================================================
    "chain/chain/src/stateless_validation/chunk_validation.rs",
    "chain/client/src/stateless_validation/state_witness_producer.rs",
    "chain/client/src/stateless_validation/validate.rs",
    "core/primitives/src/stateless_validation/state_witness.rs",
    "core/primitives/src/stateless_validation/stored_chunk_state_transition_data.rs",
    "core/primitives/src/stateless_validation/contract_distribution.rs",

    # =================================================================================
    # Staking, rewards and validator selection reachable by any account
    # =================================================================================
    "chain/epoch-manager/src/lib.rs",
    "chain/epoch-manager/src/validator_selection.rs",
    "chain/epoch-manager/src/reward_calculator.rs",
    "chain/epoch-manager/src/validator_stats.rs",
    "chain/epoch-manager/src/epoch_info_aggregator.rs",
    "chain/epoch-manager/src/adapter.rs",
    "core/primitives/src/epoch_info.rs",
    "core/primitives/src/validator_mandates/mod.rs",
    "core/primitives/src/validator_mandates/compute_price.rs",

    # =================================================================================
    # RPC and view-layer entrypoints exposed to any caller
    # =================================================================================
    "chain/jsonrpc/src/lib.rs",
    "chain/jsonrpc/src/sharded_rpc.rs",
    "chain/jsonrpc/src/api/mod.rs",
    "chain/jsonrpc/src/api/query.rs",
    "chain/jsonrpc/src/api/call_function.rs",
    "chain/jsonrpc/src/api/transactions.rs",
    "chain/jsonrpc/src/api/view_state.rs",
    "chain/jsonrpc/src/api/view_access_key.rs",
    "chain/jsonrpc/src/api/view_access_key_list.rs",
    "chain/jsonrpc/src/api/view_gas_key_nonces.rs",
    "chain/jsonrpc/src/api/changes.rs",
    "chain/jsonrpc/src/api/gas_price.rs",
    "chain/jsonrpc/src/api/receipts.rs",
    "chain/client/src/view_client_actor.rs",
    "core/primitives/src/views.rs",

    # =================================================================================
    # NEAR-developed wallet contract: Ethereum transaction emulation
    # =================================================================================
    "runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs",
    "runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs",
    "runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs",
    "runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs",
    "runtime/near-wallet-contract/implementation/wallet-contract/src/ethabi_utils.rs",
    "runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs",
    "runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs",

    # =================================================================================
    # Shared encoding, hashing and merkle primitives on validation paths
    # =================================================================================
    "core/primitives/src/utils.rs",
    "core/primitives/src/utils/compression.rs",
    "core/primitives/src/utils/io.rs",
    "core/primitives/src/merkle.rs",
    "core/primitives/src/shard_layout/mod.rs",
    "core/primitives/src/sharding.rs",
    "core/primitives/src/types.rs",
    "core/primitives-core/src/hash.rs",
]


target_scopes = [
    "Critical. An unprivileged account holder moves NEAR or contract-owned assets they were never authorized to move, because signature and nonce checks in verifier.rs, access-key permission and allowance enforcement in access_keys.rs, SignedDelegateAction sender/receiver binding and the signable-message discriminant in delegate.rs and signable_message.rs, or Ethereum transaction emulation in the NEAR wallet contract lets a transaction or meta-transaction execute under another account's authority.",
    "Critical. Total NEAR supply or an account balance changes without a matching debit, because deposit, refund, gas-refund, storage-staking, or account-deletion accounting in runtime/src/lib.rs, actions.rs, and receipt_manager.rs lets a user-submitted transaction or receipt mint tokens from nothing, double-refund a failed action, or burn balance that should have been returned.",
    "Critical. A contract executes work far beyond what it paid for, because gas metering in gas_counter.rs, the instrumentation inserted by instrument_v3.rs/prepare_v3.rs, per-op and host-function costs in core/parameters, or the attached-gas and prepaid-fee split in config.rs lets an attacker-deployed WASM module or a crafted function call run with a gas charge that does not match its real cost, bypassing fee payment and letting one account starve a shard.",
    "Critical. Two honest nodes applying the same chunk reach different state roots or outcomes, because nondeterminism in WASM execution and trap classification in the wasmtime runner, divergence between memtrie, flat storage, and disk trie reads, protocol-version or feature gating in version.rs and features.rs, or ordering in the apply loop depends on node-local state, producing an unintended permanent chain split from a single submitted transaction.",
    "Critical. An invalid state transition is accepted as valid, because trie insert/delete and squash logic, refcount handling in db/refcount.rs, memtrie node encoding and flexible-data layout, or the recorded-witness path in trie_recording.rs lets an attacker-controlled key/value pattern produce a state root that does not reflect the applied changes, or lets a witness prove a value that was never written.",
    "Critical. One transaction or receipt any user can submit permanently stops honest nodes from applying chunks, because a panic, arithmetic overflow, unwrap, or failed assertion in the runtime apply loop, action validation, trie update, or receipt deserialization leaves a poison receipt in a queue that every node re-executes forever, halting the network with no recovery short of a hard fork.",
    "Critical. A cross-shard receipt is lost, duplicated, or delivered with the wrong value, because outgoing-buffer accounting in congestion_control.rs, allowance grants in the bandwidth scheduler, receipt queue indices in receipts_column_helper.rs and outgoing_metadata.rs, or queue handling across a resharding boundary drops or replays an attacker-triggered receipt, destroying or duplicating funds in transit.",
    "High. A cheap attacker transaction makes honest chunk producers unable to produce a valid chunk or honest nodes unable to serve queries, because state-witness size and recorded-storage accounting in state_witness_producer.rs, chunk_validation.rs, and recorded_storage_counter.rs, transaction admission in prepare_transactions.rs and chain/pool, or view-call handling in the JSON-RPC and view-client paths lets a single account inflate work beyond enforced limits, stalling the shard or crashing RPC nodes.",
    "High. User funds or an account become permanently unusable, because storage_usage accounting, storage-staking checks, account and access-key deletion in actions.rs and action_validation.rs, or global-contract, deterministic-account and universal-account state initialization leaves an account below its storage bond, unable to be funded, or controlled by a code hash that can never be satisfied, permanently freezing the balance.",
    "High. A staker gains rewards or influence they did not earn, because stake and unstake action handling, locked-balance and withdrawal accounting, validator proposal processing in validator_selection.rs, mandate pricing in compute_price.rs, or uptime and reward computation in reward_calculator.rs and validator_stats.rs lets an ordinary account manipulate its effective stake, recover locked tokens early, or claim rewards attributable to others.",
    "Critical/High blind spot. An unprivileged transaction signer, contract deployer, meta-transaction sender, staker, or RPC caller abuses an assumption the protocol never wrote down: a value validated at transaction admission and trusted as already-validated at apply time, an account or contract re-derived after the check that authorized it, a limit enforced on one path but not on its cached, batched, promise-chained, or refund twin, state carried across chunk, shard, epoch, resharding, or protocol-upgrade boundaries that was only proven safe within one of them, or an error path that commits partial state - yielding unauthorized balance movement, a state root that diverges between honest nodes, or a receipt no node can ever finish applying.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one nearcore target.

    ```
    target_file format:
    "'File Name: runtime/runtime/src/verifier.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact nearcore target:

    {target_file}

    Project focus:
    nearcore is the reference NEAR Protocol client. Focus only on what an ordinary account holder reaches by signing and submitting a transaction, deploying and calling their own WASM contract, sending a meta-transaction, staking, or calling public RPC: transaction and action validation, access keys and nonces, the runtime apply loop and balance accounting, gas metering and WASM preparation, global/deterministic/universal accounts, cross-shard receipts with congestion control and the bandwidth scheduler, trie and flat-storage state, state-witness size limits, transaction admission and chunk transaction selection, staking and rewards, JSON-RPC and view calls, and the NEAR wallet contract's Ethereum emulation.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (function, method, struct, enum variant, field, host function, protocol feature) when possible.
    * Attacker is unprivileged only: any account holder who funds an account, signs and submits transactions through public RPC, deploys their own WASM contract, calls any contract, sends a SignedDelegateAction through a relayer, deploys or uses a global/deterministic/universal account, stakes their own tokens, or queries RPC. They sign only for their own keys.
    * Attacker is NOT a validator, block or chunk producer, chunk validator, node operator, relayer key holder, archival/DB owner, or holder of another user's key. Never assume a malicious peer, malicious node, malicious validator, network/gossip/sync/state-sync attacker, leaked key, compromised host, non-default config, or social engineering.
    * Out of scope, never ask about: peer-to-peer message handling, network flooding, peer discovery, block/header/state/epoch sync, block and chunk gossip, SPICE validator-only paths, sandbox or adversarial test features, node configuration, metrics, CLI, dependencies.
    * Ignore test files, mocks, fuzz harnesses, benchmarks, docs, generated code, and TOML/config-only findings.
    * Every question must describe a real transaction, receipt, contract call, or RPC request an attacker actually submits. No generic unbounded-allocation, memory-growth, cache-size, or resource-exhaustion speculation; no "what if the input is huge" questions without a concrete submitted payload and a concrete broken invariant.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target unauthorized balance or asset movement, token minting or supply inflation, fee and gas payment bypass, state-root divergence between honest nodes, acceptance of an invalid state transition, cross-shard receipt loss or duplication, permanently frozen funds, or a submitted payload that halts chunk application.
    * Every question must be testable by a Rust unit test, a runtime or near-vm-runner test, a test-loop test under test-loop-tests, or a script against the local network in tools/bounty-localnet.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Authorization is exact: an action executes only under a signature over the exact transaction or delegate-action hash, within the access key's permission, allowance, and nonce ordering.
    * Value is conserved: total supply, account balances, locked stake, storage bonds, prepaid gas and refunds balance exactly across every transaction, receipt, and shard.
    * Determinism holds: every honest node applying the same chunk against the same state produces the same state root, gas burnt, and outcomes, regardless of caching, memtrie vs disk reads, or node-local state.
    * Delivery is exact-once: every outgoing receipt is delivered to its target shard exactly once with its full value, across congestion, bandwidth limits, and resharding.
    * Metering is honest: gas charged matches work performed, and every limit enforced at admission is also enforced at apply time.
    * Execution is total: no attacker-submitted transaction, receipt, or contract can leave nodes unable to apply chunks or serve valid requests.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction, action, receipt, contract, or RPC request);
    3. preconditions (accounts, keys, balances, and contracts the attacker controls);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Rust unit/runtime/test-loop/localnet test PARAMETERS and assert AUTHORIZATION_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, EXACT_ONCE_DELIVERY, HONEST_METERING, or TOTAL_EXECUTION.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused nearcore exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any account holder who signs and submits transactions through public RPC, deploys and calls their own WASM contract, sends a meta-transaction, stakes their own tokens, or queries RPC. No validator, chunk producer, chunk validator, node operator, archival/DB, relayer-key, or foreign-key access.
- Reject malicious-peer, malicious-node, malicious-validator, p2p/gossip/sync/state-sync/network-layer, leaked-key, host-level, and misconfiguration-only paths.
- Reject SPICE validator-only paths, sandbox/adversarial test features, metrics, CLI, dependency-only, and test/mock/fuzz/bench/docs/generated/config-only findings.
- Reject generic unbounded-allocation or resource-growth claims with no concrete submitted transaction and no broken invariant.
- This program pays High and Critical only. Focus on real chain impact: unauthorized balance or asset movement, token minting or supply inflation, fee and gas payment bypass, state-root divergence between honest nodes, acceptance of an invalid state transition, cross-shard receipt loss or duplication, permanently frozen funds, or a submitted payload that halts chunk application or crashes RPC nodes.

## Validate
- Trace the exact reachable path from the attacker's transaction, receipt, contract call, or RPC request into the affected function.
- Check whether signature and nonce checks, access-key permissions, action validation, gas and storage limits, congestion and bandwidth limits, or existing error handling already stop it.
- Confirm the path is reachable on the current mainnet protocol version and active feature gates.
- Accept only concrete unauthorized value movement, supply inflation, fee bypass, state divergence, invalid state transition acceptance, receipt loss or duplication, permanent fund freezing, or a node-level halt.
- Require exact file/function support and a reproducible Rust unit, runtime, near-vm-runner, test-loop, or bounty-localnet PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker transaction inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (loss or theft of funds, supply inflation, fee bypass, consensus divergence, invalid state transition, chain halt) or High (authorization bypass, state corruption, permanently frozen funds, long-lived inability to apply chunks or serve RPC)]

### Likelihood Explanation
[Preconditions, accounts and balances needed, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Rust unit/runtime/test-loop/localnet test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for nearcore.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only analogs an unprivileged transaction signer, contract deployer, meta-transaction sender, staker, or RPC caller can reach: transaction and action validation, access keys and nonces, the runtime apply loop and balance accounting, gas metering and WASM preparation, global/deterministic/universal accounts, cross-shard receipts with congestion control and bandwidth scheduling, trie and flat-storage state, state-witness limits, transaction admission and chunk transaction selection, staking and rewards, JSON-RPC and view calls, or the NEAR wallet contract.
- Reject malicious-peer, malicious-node, malicious-validator, network-layer, sync, leaked-key, operator-only, SPICE validator-only, sandbox/adversarial, CLI, mocked-only paths, dependency-only bugs, and no-impact analogs.
- Medium , High and Critical only; no low, or resource-only analogs.

## Validate
- Map the bug class to the strongest reachable nearcore path from a single submitted transaction, contract call, or RPC request.
- Prove root cause with exact file/function support.
- Accept only concrete unauthorized value movement, supply inflation, fee or gas bypass, state-root divergence between honest nodes, invalid state transition acceptance, receipt loss or duplication, permanently frozen funds, or a transaction-triggered halt.

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
    Generate a strict bounty-style validation prompt for nearcore security claims.
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
- Reject malicious-peer, malicious-node, malicious-validator, p2p/gossip/network-layer, block/header/state/epoch sync, SPICE validator-only, sandbox/adversarial test-feature, metrics, CLI, dependency-only, docs/style, generated-file, and test/mock/fuzz/bench/config-only issues.
- Reject if the exploit needs validator, chunk-producer, chunk-validator, node-operator, host, database, or relayer-key access, another user's key, victim social engineering, a non-default configuration, or anything outside what an unprivileged account holder can put in a transaction, a deployed contract, or an RPC request.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged transaction signer, contract deployer, meta-transaction sender, staker, or RPC caller, unless the claim proves escalation from that starting point.
- The final impact must map to an in-scope category: Critical - unauthorized transfer or theft of NEAR or contract assets, token minting or supply inflation, fee or gas payment bypass, state-root divergence between honest nodes, acceptance of an invalid state transition, cross-shard receipt loss or duplication, or a chain halt; High - access-key or authorization bypass, corruption of account, trie, receipt, or stake state, permanently frozen funds, reward manipulation, or long-lived inability of honest nodes to apply chunks or serve RPC.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken authorization, value-conservation, determinism, exact-once-delivery, metering, or total-execution invariant.
3. Reachable exploit path: preconditions (attacker-controlled accounts, keys, balances, contracts) -> submitted transaction, receipt, contract call, or RPC request -> trigger -> bad result.
4. Existing signature and nonce checks, access-key permissions, action validation, gas and storage limits, congestion and bandwidth limits, and error handling reviewed and shown insufficient.
5. Concrete in-scope High/Critical impact with realistic likelihood.
6. Reproducible proof path: Rust unit PoC, runtime or near-vm-runner test, test-loop test, or exact steps against the local network in tools/bounty-localnet.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary account holder trigger this with a transaction, contract, or RPC call, without validator, operator, host, or foreign-key access?
- Does the code actually behave as claimed under the current mainnet protocol version and active feature gates?
- Is the impact caused by this code, not by a malicious peer, validator, or dependency?
- Is the theft, inflation, divergence, freeze, or halt concrete rather than hypothetical?
- Would a NEAR triager accept the proof-of-concept?
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
[Concrete in-scope impact, severity rationale, and NEAR bounty category]

## Likelihood Explanation
[Attacker capability, accounts and balances required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Rust unit/runtime/test-loop/localnet test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
