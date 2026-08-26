import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'anza-xyz/agave'
# todo: the name of the repository
REPO_NAME = 'agave'

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
    # Transaction admission: sanitization, signature verification, replay protection
    # =================================================================================
    "runtime-transaction/src/runtime_transaction/sdk_transactions.rs",
    "runtime-transaction/src/runtime_transaction/transaction_view.rs",
    "runtime-transaction/src/instruction_data_len.rs",
    "runtime-transaction/src/signature_details.rs",
    "runtime-transaction/src/sanitize_config.rs",
    "perf/src/sigverify.rs",
    "precompiles/src/lib.rs",
    "precompiles/src/ed25519.rs",
    "precompiles/src/secp256k1.rs",
    "precompiles/src/secp256r1.rs",
    "runtime/src/bank/check_transactions.rs",
    "runtime/src/status_cache.rs",
    "runtime/src/bank/address_lookup_table.rs",
    "accounts-db/src/blockhash_queue.rs",
    "accounts-db/src/account_locks.rs",
    "accounts-db/src/accounts.rs",

    # =================================================================================
    # Fees, compute budget and block cost accounting on attacker-declared limits
    # =================================================================================
    "compute-budget-instruction/src/compute_budget_instruction_details.rs",
    "compute-budget-instruction/src/instructions_processor.rs",
    "compute-budget-instruction/src/builtin_programs_filter.rs",
    "compute-budget/src/compute_budget_limits.rs",
    "fee/src/lib.rs",
    "cost-model/src/cost_model.rs",
    "cost-model/src/cost_tracker.rs",

    # =================================================================================
    # SVM: account loading, rent, nonce rollback, state commit
    # =================================================================================
    "svm/src/account_loader.rs",
    "svm/src/transaction_account_state_info.rs",
    "svm/src/rent_calculator.rs",
    "svm/src/transaction_processor.rs",
    "svm/src/program_loader.rs",
    "svm/src/nonce_info.rs",
    "svm/src/rollback_accounts.rs",
    "runtime/src/account_saver.rs",
    "runtime/src/rent_collector.rs",

    # =================================================================================
    # Program runtime: invoke context, CPI privileges, VM memory and serialization
    # =================================================================================
    "program-runtime/src/invoke_context.rs",
    "program-runtime/src/cpi.rs",
    "program-runtime/src/serialization.rs",
    "program-runtime/src/memory.rs",
    "program-runtime/src/memory_context.rs",
    "program-runtime/src/vm.rs",
    "program-runtime/src/execution_budget.rs",
    "program-runtime/src/loaded_programs.rs",
    "program-runtime/src/program_cache_entry.rs",
    "program-runtime/src/deploy.rs",
    "program-runtime/src/sysvar_cache.rs",
    "program-runtime/src/mem_pool.rs",

    # =================================================================================
    # Transaction context: account privileges, borrows and VM address translation
    # =================================================================================
    "transaction-context/src/lib.rs",
    "transaction-context/src/transaction.rs",
    "transaction-context/src/transaction_accounts.rs",
    "transaction-context/src/instruction.rs",
    "transaction-context/src/instruction_accounts.rs",
    "transaction-context/src/vm_slice.rs",
    "transaction-context/src/vm_addresses.rs",

    # =================================================================================
    # Syscalls reachable from any attacker-deployed SBF program
    # =================================================================================
    "syscalls/src/lib.rs",
    "syscalls/src/cpi.rs",
    "syscalls/src/mem_ops.rs",
    "syscalls/src/sysvar.rs",
    "syscalls/src/logging.rs",

    # =================================================================================
    # Builtin programs invoked directly by attacker instructions
    # =================================================================================
    "programs/system/src/system_processor.rs",
    "programs/system/src/system_instruction.rs",
    "programs/bpf_loader/src/lib.rs",
    "programs/vote/src/vote_processor.rs",
    "programs/vote/src/vote_state/mod.rs",
    "programs/vote/src/vote_state/handler.rs",
    "programs/compute-budget/src/lib.rs",
    "programs/zk-elgamal-proof/src/lib.rs",

    # =================================================================================
    # Bank state consensus surface mutated by transactions: hash, stakes, rewards
    # =================================================================================
    "runtime/src/bank.rs",
    "runtime/src/bank/accounts_lt_hash.rs",
    "runtime/src/bank/fee_distribution.rs",
    "runtime/src/transaction_execution.rs",
    "runtime/src/bank/recent_blockhashes_account.rs",
    "runtime/src/bank/partitioned_epoch_rewards/calculation.rs",
    "runtime/src/bank/partitioned_epoch_rewards/distribution.rs",
    "runtime/src/inflation_rewards/points.rs",
    "runtime/src/stakes.rs",
    "runtime/src/stake_account.rs",
    "runtime/src/sysvar_account.rs",
]


target_scopes = [
    "Critical. An unprivileged fee-payer submits a transaction whose instruction mutates lamports, data, or owner of an account it never signed for, because signer/writable privilege bookkeeping in transaction-context, duplicate-account dedup, or CPI privilege propagation widens privileges, giving theft of funds without the victim's signature.",
    "Critical. An unprivileged sender forges a signature accepted by transaction sigverify or an ed25519/secp256k1/secp256r1 precompile via crafted instruction-data offsets, counts, or recovery parameters, so a program that trusts precompile verification authorizes a transfer the key owner never approved.",
    "Critical. An unprivileged sender breaks lamport conservation in a single transaction or block, minting or destroying lamports through account state-change accounting, rent collection, fee charging, rollback/nonce restoration, or reward distribution, inflating supply or draining balances.",
    "Critical. An unprivileged sender gets an already-processed transaction executed a second time, or gets a durable-nonce transaction replayed, by defeating status-cache dedup, blockhash-queue age checks, or nonce advance/rollback ordering, double-spending the transfer.",
    "Critical. An unprivileged sender takes ownership of an account or program another authority controls through the system program (assign, create_account, create_with_seed, allocate) or bpf_loader upgrade/authority/buffer checks, then drains or reprograms it.",
    "Critical. An unprivileged sender crafts a transaction whose execution is nondeterministic or state-dependent across validators (program cache reuse, sysvar snapshot, account lt-hash accumulation, feature-gated branch, iteration order), producing a bank hash mismatch, fork, or optimistically confirmed slot that is later invalid.",
    "Critical. An unprivileged sender drives the vote program into a state transition that mis-attributes credits, stake, or authorized voter/withdrawer, or lets a vote account be updated in a way that corrupts consensus accounting or lets delegated stake be withdrawn by the wrong key.",
    "Critical. An unprivileged sender lands a single transaction that panics, aborts, or fails to terminate during replay (unwrap, index out of range, unchecked arithmetic overflow, unbounded recursion), halting every validator on that block and requiring human intervention.",
    "High. An unprivileged sender executes a transaction while paying less than the fee, rent, or compute it actually consumes, by manipulating compute-budget instruction parsing, CU metering across CPI depth and heap requests, or fee/prioritization computation, extracting free execution and starving the fee market.",
    "High. An unprivileged sender submits transactions whose real validator cost far exceeds what the cost model, CU meter, or account-lock accounting charges (program load and ELF verification, account data resizing, syscall work, lookup-table expansion), exhausting block capacity or node resources and degrading liveness.",
    "High. An unprivileged sender exploits a protocol blind spot the design never anticipated: an unmodelled interaction between two individually correct components (loaded-program cache vs redeployment, lookup-table resolution vs account locks, nonce vs fee payer rollback, rent vs state-size accounting, sysvar refresh vs mid-block reads) where each side's assumption holds alone but their composition breaks value conservation, determinism, or privilege exactness.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one agave target.

    ```
    target_file format:
    "'File Name: svm/src/account_loader.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact agave target:

    {target_file}

    Project focus:
    agave is the Anza Solana validator client. Focus on what a transaction submitted by any internet client reaches: transaction sanitization and sigverify, precompiles, blockhash/nonce replay protection, address lookup tables, fee and compute-budget accounting, SVM account loading and state commit, program runtime CPI and VM memory, syscalls, builtin programs (system, vote, bpf_loader, compute-budget), and the bank state those transactions mutate.

    Rules:
    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (fn, method, struct, field, const) when possible.
    * Attacker is unprivileged only: an ordinary client that funds a keypair, signs and submits transactions to a public RPC/TPU endpoint, deploys its own SBF program, and fully controls instruction data, account lists, lookup tables, and compute-budget instructions.
    * Attacker is NOT a validator operator, leader, gossip/turbine/repair peer, RPC operator, geyser plugin, or snapshot provider. Ignore malicious-node, malicious-peer, network-layer, shred, snapshot, CLI, config, and social-engineering assumptions.
    * Ignore tests, benches, mocks, fuzz and conformance harnesses, docs, generated files, and dependency-only issues.
    * Out of scope: votor/Alpenglow crates, Loader V4, the non-JIT VM interpreter, geyser and scheduler-bindings, snapshot loading, metrics, and bootstrap-phase-only issues.
    * Only consider paths reachable under the default activated feature set.
    * Every question must be a concrete real-world scenario an unprivileged sender can perform on mainnet. No speculative "unbounded memory/allocation" or resource-hygiene questions unless the scope explicitly targets cost accounting.
    * Generate 30 to 40 high-signal questions.
    * At least 70% must target theft of funds without the owner's signature, lamport creation/destruction, double-spend or replay, privilege escalation across accounts or CPI, consensus divergence/bank hash mismatch, or a replay-path panic that halts the cluster.
    * Every question must be testable by a Rust unit test, an SVM/bank integration test, or a differential/table test.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Privilege exactness: only accounts marked signer and writable by the transaction can be mutated, and CPI never grants a callee privileges the caller did not hold.
    * Value conservation: total lamports change only by declared fees, rent, and inflation; no transaction executes twice.
    * Determinism: the same bank and transaction produce identical results, account state, and bank hash on every validator.
    * Metering totality: every compute unit, byte, and account touched is charged and bounded before it is consumed.
    * Panic freedom: no attacker-controlled input reaches a panic, overflow, or non-terminating loop on the replay path.

    Each question must include:
    1. target function/method;
    2. attacker action (a concrete transaction or instruction);
    3. preconditions (funded keypair, deployed program, account state);
    4. instruction/transaction sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger TRANSACTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: unit/integration test PARAMETERS and assert PRIVILEGE_EXACTNESS, VALUE_CONSERVATION, DETERMINISM, METERING_TOTALITY, or PANIC_FREEDOM.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused agave exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: an ordinary client that signs and submits transactions to a public RPC/TPU endpoint and may deploy its own SBF program. No validator, leader, gossip/turbine peer, RPC operator, geyser, or snapshot access; no leaked keys or social engineering.
- Reject malicious-node, malicious-peer, network-layer, shred, snapshot, operator-only, and misconfiguration-only paths.
- Reject votor/Alpenglow, Loader V4, non-JIT interpreter, geyser, metrics, bootstrap-phase, test/mock/bench/generated-file, and dependency-only findings.
- Reject speculative resource-hygiene claims with no reachable mainnet scenario.
- Focus on real impact: theft of funds without the owner's signature, lamport inflation or loss, double-spend/replay, cross-account or CPI privilege escalation, consensus divergence, or a cluster-halting panic.

## Validate
- Trace the exact reachable path from the attacker's transaction (instruction data, account list, lookup table, compute-budget, program bytecode) into the affected function.
- Check whether existing signer/writable checks, sanitization, feature gates, metering, or replay protection already stop it.
- Accept only a concrete loss of funds, consensus/safety violation, or liveness halt caused by this code.
- Require exact file/function support and a reproducible Rust unit or bank/SVM integration test PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker transaction inputs, exploit flow, and why checks fail]

### Impact Explanation
[Concrete scoped impact and matching Solana bounty category]

### Likelihood Explanation
[Preconditions, cost to the attacker, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Unit/integration test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for agave security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject malicious-node, malicious-peer, network/gossip/shred-layer, snapshot, operator-only, misconfiguration, leaked-key, dependency-only, docs/style, generated-file, and test/mock/bench-only issues.
- Reject votor/Alpenglow, Loader V4, non-JIT interpreter, geyser and scheduler-bindings, metrics, and bootstrap-phase-only findings.
- Reject if the exploit needs validator, leader, RPC-operator, or plugin privileges, victim social engineering, an impossible setup, or anything beyond what an ordinary client can put in a transaction.
- Reject if the bug was fixed, acknowledged, or publicly disclosed already, per the eligibility rules.
- A valid report must be triggerable by an unprivileged sender submitting transactions on a default-configured mainnet-like cluster.
- The final impact must map to an in-scope Solana category: loss of funds, consensus/safety violation, or liveness loss requiring human intervention.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and broken security assumption.
3. Reachable exploit path: preconditions -> attacker transaction -> trigger -> bad result.
4. Existing signer/writable checks, sanitization, replay protection, metering, and feature gates reviewed and shown insufficient.
5. Concrete in-scope impact with realistic likelihood and attacker cost.
6. Reproducible proof path: Rust unit PoC, bank/SVM integration test, or exact transaction steps against a local validator.
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an ordinary funded client trigger this with a transaction, without validator, leader, or operator access?
- Does the code actually behave as claimed under the default activated feature set?
- Is the impact caused by this code, not by a malicious node, peer, or dependency alone?
- Is the theft, inflation, replay, divergence, or halt concrete rather than hypothetical?
- Would an Anza triager accept the proof?
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
[Concrete in-scope impact, severity rationale, and Solana bounty category]

## Likelihood Explanation
[Attacker capability, preconditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or unit/integration test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for agave.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-sender analogs in transaction sanitization, sigverify and precompiles, replay protection, lookup tables, fee and compute-budget accounting, SVM account loading and commit, program runtime CPI and VM memory, syscalls, builtin programs, or bank consensus state.
- Reject malicious-node, malicious-peer, network-layer, snapshot, operator-only, votor/Alpenglow, Loader V4, interpreter, mocked-only paths, dependency-only bugs, and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable agave path from an ordinary client's transaction.
- Prove root cause with exact file/function support.
- Accept only concrete theft of funds, lamport inflation or loss, double-spend/replay, CPI or account privilege escalation, consensus divergence, or a cluster-halting panic.

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
