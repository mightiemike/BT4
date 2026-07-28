import json
import os

MAX_REPO = 40
SOURCE_REPO = 'pushchain/push-chain-node'
REPO_NAME = 'push-chain-node'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
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
    'app/ante/account_init_decorator.go',
    'app/ante/ante.go',
    'app/ante/ante_cosmos.go',
    'app/ante/ante_evm.go',
    'app/ante/fee.go',
    'app/ante/handler_options.go',
    'app/ante/validator_tx_fee.go',
    'app/cosmos/min_gas_price.go',
    'app/txpolicy/gasless.go',
    'precompiles/usigverifier/USigVerifier.sol',
    'precompiles/usigverifier/query.go',
    'precompiles/usigverifier/usigverifier.go',
    'universalClient/api/handlers.go',
    'universalClient/api/routes.go',
    'universalClient/api/server.go',
    'universalClient/chains/chains.go',
    'universalClient/chains/common/chain_store.go',
    'universalClient/chains/common/event_cleaner.go',
    'universalClient/chains/common/event_processor.go',
    'universalClient/chains/common/types.go',
    'universalClient/chains/evm/chain_meta_oracle.go',
    'universalClient/chains/evm/client.go',
    'universalClient/chains/evm/event_confirmer.go',
    'universalClient/chains/evm/event_listener.go',
    'universalClient/chains/evm/event_parser.go',
    'universalClient/chains/evm/rpc_client.go',
    'universalClient/chains/evm/tx_builder.go',
    'universalClient/chains/push/client.go',
    'universalClient/chains/push/event_listener.go',
    'universalClient/chains/push/event_parser.go',
    'universalClient/chains/svm/chain_meta_oracle.go',
    'universalClient/chains/svm/client.go',
    'universalClient/chains/svm/event_confirmer.go',
    'universalClient/chains/svm/event_listener.go',
    'universalClient/chains/svm/event_parser.go',
    'universalClient/chains/svm/rent_reclaimer.go',
    'universalClient/chains/svm/rpc_client.go',
    'universalClient/chains/svm/tx_builder.go',
    'universalClient/config/config.go',
    'universalClient/config/types.go',
    'universalClient/core/client.go',
    'universalClient/db/db.go',
    'universalClient/logger/logger.go',
    'universalClient/pushcore/pushCore.go',
    'universalClient/pushsigner/grant_verifier.go',
    'universalClient/pushsigner/keys/keys.go',
    'universalClient/pushsigner/pushsigner.go',
    'universalClient/pushsigner/vote.go',
    'universalClient/store/models.go',
    'universalClient/tss/coordinator/coordinator.go',
    'universalClient/tss/coordinator/msg_handler.go',
    'universalClient/tss/coordinator/types.go',
    'universalClient/tss/coordinator/utils.go',
    'universalClient/tss/dkls/keygen.go',
    'universalClient/tss/dkls/keyrefresh.go',
    'universalClient/tss/dkls/quorumchange.go',
    'universalClient/tss/dkls/sign.go',
    'universalClient/tss/dkls/types.go',
    'universalClient/tss/dkls/utils.go',
    'universalClient/tss/eventstore/store.go',
    'universalClient/tss/expirysweeper/sweeper.go',
    'universalClient/tss/keyshare/manager.go',
    'universalClient/tss/networking/libp2p/config.go',
    'universalClient/tss/networking/libp2p/network.go',
    'universalClient/tss/networking/message.go',
    'universalClient/tss/networking/peer.go',
    'universalClient/tss/networking/types.go',
    'universalClient/tss/sessionmanager/sessionmanager.go',
    'universalClient/tss/tss.go',
    'universalClient/tss/txbroadcaster/broadcaster.go',
    'universalClient/tss/txbroadcaster/evm.go',
    'universalClient/tss/txbroadcaster/svm.go',
    'universalClient/tss/txflow/nonce.go',
    'universalClient/tss/txflow/parse.go',
    'universalClient/tss/txflow/types.go',
    'universalClient/tss/txresolver/evm.go',
    'universalClient/tss/txresolver/resolver.go',
    'universalClient/tss/txresolver/svm.go',
    'x/uexecutor/autocli.go',
    'x/uexecutor/depinject.go',
    'x/uexecutor/keeper/admin_revert.go',
    'x/uexecutor/keeper/ballot_hooks.go',
    'x/uexecutor/keeper/build_revert_outbound.go',
    'x/uexecutor/keeper/chain_meta.go',
    'x/uexecutor/keeper/create_outbound.go',
    'x/uexecutor/keeper/deploy_uea.go',
    'x/uexecutor/keeper/evm.go',
    'x/uexecutor/keeper/evm_hooks.go',
    'x/uexecutor/keeper/execute_inbound.go',
    'x/uexecutor/keeper/execute_inbound_funds.go',
    'x/uexecutor/keeper/execute_inbound_funds_and_payload.go',
    'x/uexecutor/keeper/execute_inbound_gas.go',
    'x/uexecutor/keeper/execute_inbound_gas_and_payload.go',
    'x/uexecutor/keeper/execute_payload.go',
    'x/uexecutor/keeper/fees.go',
    'x/uexecutor/keeper/gas_fee.go',
    'x/uexecutor/keeper/gas_price.go',
    'x/uexecutor/keeper/genesis.go',
    'x/uexecutor/keeper/handle_failed_inbound_validation.go',
    'x/uexecutor/keeper/handler.go',
    'x/uexecutor/keeper/inbound.go',
    'x/uexecutor/keeper/keeper.go',
    'x/uexecutor/keeper/msg_deploy_uea.go',
    'x/uexecutor/keeper/msg_execute_payload.go',
    'x/uexecutor/keeper/msg_migrate_uea.go',
    'x/uexecutor/keeper/msg_server.go',
    'x/uexecutor/keeper/msg_update_params.go',
    'x/uexecutor/keeper/msg_vote_inbound.go',
    'x/uexecutor/keeper/msg_vote_outbound.go',
    'x/uexecutor/keeper/outbound.go',
    'x/uexecutor/keeper/pending_outbound.go',
    'x/uexecutor/keeper/pending_outbound_query.go',
    'x/uexecutor/keeper/query_keys.go',
    'x/uexecutor/keeper/query_server.go',
    'x/uexecutor/keeper/query_server_v2.go',
    'x/uexecutor/keeper/universal_tx.go',
    'x/uexecutor/keeper/uvalidator_hooks.go',
    'x/uexecutor/keeper/voting.go',
    'x/uexecutor/migrations/v2/migrate.go',
    'x/uexecutor/migrations/v4/migrate.go',
    'x/uexecutor/migrations/v5/migrate.go',
    'x/uexecutor/module.go',
    'x/uexecutor/types/abi.go',
    'x/uexecutor/types/caip2.go',
    'x/uexecutor/types/chain_meta.go',
    'x/uexecutor/types/codec.go',
    'x/uexecutor/types/constants.go',
    'x/uexecutor/types/decode_payload.go',
    'x/uexecutor/types/events.go',
    'x/uexecutor/types/expected_keepers.go',
    'x/uexecutor/types/gas_price.go',
    'x/uexecutor/types/gateway_pc_event_decode.go',
    'x/uexecutor/types/genesis.go',
    'x/uexecutor/types/inbound.go',
    'x/uexecutor/types/keys.go',
    'x/uexecutor/types/migration_payload.go',
    'x/uexecutor/types/msg_execute_payload.go',
    'x/uexecutor/types/msg_migrate_uea.go',
    'x/uexecutor/types/msg_vote_chain_meta.go',
    'x/uexecutor/types/msg_vote_inbound.go',
    'x/uexecutor/types/msg_vote_outbound.go',
    'x/uexecutor/types/outbound_tx.go',
    'x/uexecutor/types/params.go',
    'x/uexecutor/types/pc_tx.go',
    'x/uexecutor/types/status.go',
    'x/uexecutor/types/tx_type.go',
    'x/uexecutor/types/universal_account_id.go',
    'x/uexecutor/types/universal_payload.go',
    'x/uexecutor/types/universal_tx.go',
    'x/uregistry/autocli.go',
    'x/uregistry/depinject.go',
    'x/uregistry/keeper/genesis.go',
    'x/uregistry/keeper/keeper.go',
    'x/uregistry/keeper/msg_add_chain_config.go',
    'x/uregistry/keeper/msg_add_token_config.go',
    'x/uregistry/keeper/msg_remove_token_config.go',
    'x/uregistry/keeper/msg_server.go',
    'x/uregistry/keeper/msg_update_chain_config.go',
    'x/uregistry/keeper/msg_update_params.go',
    'x/uregistry/keeper/msg_update_token_config.go',
    'x/uregistry/keeper/query_server.go',
    'x/uregistry/migrations/v2/migrate.go',
    'x/uregistry/migrations/v3/migrate.go',
    'x/uregistry/module.go',
    'x/uregistry/types/block_confirmation.go',
    'x/uregistry/types/chain_config.go',
    'x/uregistry/types/chain_enabled.go',
    'x/uregistry/types/codec.go',
    'x/uregistry/types/constants.go',
    'x/uregistry/types/expected_keepers.go',
    'x/uregistry/types/gateway_methods.go',
    'x/uregistry/types/genesis.go',
    'x/uregistry/types/keys.go',
    'x/uregistry/types/msg_add_chain_config.go',
    'x/uregistry/types/msg_add_token_config.go',
    'x/uregistry/types/msg_remove_token_config.go',
    'x/uregistry/types/msg_update_chain_config.go',
    'x/uregistry/types/msg_update_params.go',
    'x/uregistry/types/msg_update_token_config.go',
    'x/uregistry/types/native_representation.go',
    'x/uregistry/types/params.go',
    'x/uregistry/types/token_config.go',
    'x/uregistry/types/vault_methods.go',
    'x/utss/autocli.go',
    'x/utss/depinject.go',
    'x/utss/keeper/hooks.go',
    'x/utss/keeper/initiate_tss_key_process.go',
    'x/utss/keeper/keeper.go',
    'x/utss/keeper/msg_initiate_fund_migration.go',
    'x/utss/keeper/msg_server.go',
    'x/utss/keeper/msg_update_params.go',
    'x/utss/keeper/msg_vote_fund_migration.go',
    'x/utss/keeper/msg_vote_tss_key_process.go',
    'x/utss/keeper/query_server.go',
    'x/utss/keeper/tss_events.go',
    'x/utss/keeper/tss_key.go',
    'x/utss/keeper/tss_key_process.go',
    'x/utss/keeper/voting.go',
    'x/utss/module.go',
    'x/utss/types/codec.go',
    'x/utss/types/constants.go',
    'x/utss/types/events.go',
    'x/utss/types/expected_keepers.go',
    'x/utss/types/genesis.go',
    'x/utss/types/keys.go',
    'x/utss/types/msg_tss_key_process.go',
    'x/utss/types/msg_update_params.go',
    'x/utss/types/msg_vote_fund_migration.go',
    'x/utss/types/params.go',
    'x/utss/types/tss_key.go',
    'x/utss/types/tss_key_process.go',
    'x/uvalidator/abci.go',
    'x/uvalidator/autocli.go',
    'x/uvalidator/depinject.go',
    'x/uvalidator/keeper/ballot.go',
    'x/uvalidator/keeper/hooks.go',
    'x/uvalidator/keeper/keeper.go',
    'x/uvalidator/keeper/msg_add_universal_validator.go',
    'x/uvalidator/keeper/msg_remove_universal_validator.go',
    'x/uvalidator/keeper/msg_server.go',
    'x/uvalidator/keeper/msg_update_params.go',
    'x/uvalidator/keeper/msg_update_universal_validator.go',
    'x/uvalidator/keeper/msg_update_universal_validator_status.go',
    'x/uvalidator/keeper/query_server.go',
    'x/uvalidator/keeper/staking_hooks.go',
    'x/uvalidator/keeper/validator.go',
    'x/uvalidator/keeper/voting.go',
    'x/uvalidator/module.go',
    'x/uvalidator/types/ballot.go',
    'x/uvalidator/types/codec.go',
    'x/uvalidator/types/expected_keepers.go',
    'x/uvalidator/types/genesis.go',
    'x/uvalidator/types/hooks.go',
    'x/uvalidator/types/identity_info.go',
    'x/uvalidator/types/keys.go',
    'x/uvalidator/types/lifecyle_event.go',
    'x/uvalidator/types/lifecyle_info.go',
    'x/uvalidator/types/msg_add_universal_validator.go',
    'x/uvalidator/types/msg_remove_universal_validator.go',
    'x/uvalidator/types/msg_update_params.go',
    'x/uvalidator/types/msg_update_universal_validator.go',
    'x/uvalidator/types/msg_update_universal_validator_status.go',
    'x/uvalidator/types/network_info.go',
    'x/uvalidator/types/params.go',
    'x/uvalidator/types/universal_validator.go',
]

target_scopes = [
    'Critical. An unprivileged external user can steal, mint, release, refund, revert, or permanently freeze funds by making Push Chain finalize the wrong inbound, outbound, payload, PRC20 accounting change, or module-originated EVM call.',
    'Critical. An unprivileged external user can execute a victim UEA or CEA action, bypass payload authorization, misuse verification data, or corrupt the module account manual nonce so unauthorized execution or value transfer occurs.',
    'Critical. An unprivileged external user can make honest validators or honest node logic accept forged, replayed, mismatched, or cross-linked ballot, chain-meta, TSS, migration, inbound, or outbound state without assuming malicious peers, nodes, validators, or admins.',
    'Critical. An unprivileged external user can corrupt canonical asset or chain mapping so the wrong token, wrong chain, wrong recipient, wrong revert target, or wrong refund destination is used, causing theft, stuck funds, or irreversible accounting loss.',
    'Critical. An unprivileged external user can bypass gasless admission, ante validation, signature verification, or precompile checks to obtain unauthorized state transitions, fee bypass with material follow-on impact, or forged cross-chain identity verification.',
    'High. An unprivileged external user can trigger a non-network-level denial of service through gasless transactions, payload execution, malformed proof or precompile input, or state-amplifying execution that materially stalls finalization or execution.',
]

PUSH_CHAIN_ALLOWED_IMPACT_SCOPE = """## Push Chain Allowed Impact Gate
Model the live Push Chain L1 scope around `universalClient/`, the custom node code in `x/`, `precompiles/usigverifier/`, and the custom ante or gasless transaction controls. Constrain every question to an unprivileged external attacker.

In scope:
- stealing, draining, permanent loss, permanent freezing, unauthorized mint, unauthorized burn, unauthorized release, or unauthorized refund of user or protocol-controlled funds.
- unauthorized UEA or CEA execution, unauthorized module-originated EVM execution, or unauthorized state transitions in universal execution flows.
- forged, replayed, mismatched, or cross-linked inbound, outbound, ballot, chain-meta, TSS, or migration state accepted through user-reachable flows with honest validators and honest nodes.
- corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting, nonce progression, revert destination, chain config use, token mapping, or canonical UniversalTx state.
- consensus or state-machine divergence reachable from ordinary user deposits, payloads, contracts, or default transaction submission paths alone.
- denial of service only when it is not network-level and is reachable without privileged control.

Out of scope:
- malicious peers, malicious nodes, malicious validators, malicious universal validators, malicious TSS participants, malicious relayers, admin or governance abuse, key compromise, or any privileged actor assumption.
- external chain compromise or oracle dishonesty unless scoped code fails to authenticate, bind, or validate attacker-controlled data.
- tests, mocks, fixtures, docs, scripts, README files, generated protobuf output, CLI-only tooling, config JSON, TOML, dependency-only behavior, style issues, and purely informational inconsistencies."""

PUSH_CHAIN_AUDIT_PIVOTS = """## Smart Audit Pivots
- Universal execution path: inbound voting to finalization, UTX mutation, payload execution, outbound creation, revert flow, refund flow, and module-originated `DerivedEVMCall` paths must preserve authorization, nonce, and fund invariants.
- Registry and accounting path: chain config, token config, PRC20/native representation, gas token selection, gas-price or chain-meta use, and revert instructions must not misroute value or attach the wrong asset semantics.
- Honest-validator finalization path: user-created source events, payloads, and chain interactions must not let honest UVs converge on the wrong ballot, wrong variant, wrong outbound, wrong TSS event, or wrong migration outcome.
- Admission and cryptographic path: gasless allowlisting, authz wrapping, ante checks, first-use account initialization, message validation, and the Ed25519 signature-verifier precompile must not turn attacker input into accepted authorization.
- State safety path: only ask consensus or DoS questions when ordinary unprivileged user actions alone can make honest nodes diverge, halt material execution, or persist invalid canonical state."""


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one Push Chain target.
    """

    prompt = f"""
    Generate Push Chain L1 security questions for this exact target file:

    {target_file}

    Project lens:
    Push Chain is a Cosmos SDK + EVM node plus the Universal Client, with custom universal execution, registry, validator voting, TSS coordination, gasless transaction admission, chain observers, event parsers, relaying logic, and an Ed25519 verification precompile. Focus on unprivileged user entry via ordinary deposits that honest validators later observe, direct gasless message submission, default transaction submission, payload delivery, user-controlled source-chain events, and user-controlled contract or calldata inputs. Never assume a malicious peer, node, validator, UV, TSS signer, relayer, admin, or governance actor.

    Impact gate:
    {PUSH_CHAIN_ALLOWED_IMPACT_SCOPE}

    {PUSH_CHAIN_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code.
    * The attacker is strictly unprivileged and must act through normal user accounts, user-controlled source-chain actions, gasless submission, payloads, contract inputs, or standard transaction submission paths.
    * Do not rely on malicious peers, malicious nodes, colluding validators, forged UV votes, admin control, governance control, relayer control, or offchain infrastructure compromise.
    * Exclude tests, mocks, fixtures, scripts, docs, generated files, CLI-only code, config files, and dependency-only behavior.
    * Generate 18 to 24 high-signal questions with non-overlapping root causes.
    * Name the exact corrupted value: PRC20 balance, native balance, UTX record, outbound status, revert recipient, refund amount, module account nonce, gasless admission decision, validator ballot tally, chain meta, TSS key record, migration status, token mapping, or signature verification result.
    * Every question must be testable with a Go unit, integration, property, or fuzz-style test.

    Each question must include target symbol, attacker-controlled input, required state, call path, broken invariant, corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_method] Can attacker-controlled INPUT under REQUIRED_STATE reach CALL_PATH and violate INVARIANT, corrupting EXACT_VALUE_AT_RISK with scoped impact SCOPE_IMPACT? Proof idea: write a Go test that drives ENTRYPOINT through the vulnerable state transition and asserts EXPECTED_SAFETY_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Push Chain exploit-question validation prompt.
    """
    return f"""# PUSH CHAIN QUESTION REVIEW

## Exploit Question
{question}

## Scope Rules
- Audit only Push Chain production code in scoped surfaces: `universalClient/`, `x/`, `precompiles/usigverifier/`, `app/ante/`, `app/cosmos/min_gas_price.go`, and `app/txpolicy/gasless.go`.
- Ignore tests, mocks, fixtures, scripts, docs, README files, generated artifacts, CLI-only tooling, and config-only issues.
- Do not ask for repo contents or claim files are missing.

## Objective
Decide whether the question leads to a real Push Chain L1 vulnerability. The attacker must be unprivileged and must enter through ordinary user deposits, gasless submission, payload execution, contract input, or standard transaction submission flows exposed by scoped code.

Reject claims needing malicious peers, nodes, validators, UVs, TSS participants, relayers, admins, governance, or other privileged operators. Prefer #NoVulnerability unless the path proves material fund loss, unauthorized execution, accounting corruption, forged accepted protocol state, consensus failure, signature-verification failure, or non-network-level DoS.

## Required Impacts
{PUSH_CHAIN_ALLOWED_IMPACT_SCOPE}

{PUSH_CHAIN_AUDIT_PIVOTS}

## Method
1. Trace the unprivileged entrypoint.
2. Map it to exact scoped files and functions.
3. Follow the full path through validation, state mutation, EVM execution, voting or finalization logic, and final fund or state effects.
4. Identify the exact corrupted value and who loses funds, authority, or liveness.
5. Reject if honest-validator assumptions are broken, existing guards preserve the invariant, or impact is immaterial.

## Reject Immediately
- Any claim that depends on malicious peers, malicious nodes, malicious validators, malicious UVs, malicious TSS signers, admin or gov abuse, or offchain infra control.
- Honest external chain or relayer behavior unless scoped validation or binding is missing.
- Fee-only nuisance issues, view-only inconsistencies, logs, style, dependency-only behavior, or crashes without an in-scope impact.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for Push Chain issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search Push Chain's scoped node code for a native analog with concrete repository impact in universal execution, registry, ballot finalization, TSS coordination, gasless admission, or signature verification.

## Required Impacts
{PUSH_CHAIN_ALLOWED_IMPACT_SCOPE}

{PUSH_CHAIN_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, unprivileged trigger, broken invariant, exact corrupted value, and matching target scope or allowed impact. Reject privileged assumptions, malicious peer or node assumptions, external-system-only issues, network-level DoS, and dependency-only behavior.

## Work Plan
1. Classify the external bug into one Push Chain invariant.
2. Map it to exact scoped files and functions.
3. Trace attacker input through production validation, voting, execution, accounting, or precompile logic.
4. Identify the wrong balance, nonce, UTX state, ballot state, token mapping, signature verification result, or TSS or migration record.
5. Reject if existing guards preserve the invariant or the impact is not material.

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
    Generate a strict Push Chain validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against Push Chain production code in the scoped surfaces.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged external attacker using only capabilities exposed by scoped code.
- Trusted key compromise, malicious deployment, malicious peers or nodes, malicious validators or UVs, malicious TSS signers, admin or governance control, and off-repo infra control are out unless scoped code fails to authenticate, bind, or validate attacker-controlled data.
- Reject tests, mocks, fixtures, scripts, docs-only issues, generated-file issues, config-only issues, network-level DoS, fee-only nuisance issues, style issues, and dependency-only bugs.
- The final impact must match one `target_scopes` item or allowed impact below and identify the exact corrupted value.

## Required Impacts
{PUSH_CHAIN_ALLOWED_IMPACT_SCOPE}

{PUSH_CHAIN_AUDIT_PIVOTS}

## Required Checks
1. Exact file and function references in scoped code.
2. Clear broken Push Chain invariant tied to funds, execution authorization, protocol finalization integrity, signature verification, or canonical state correctness.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> bad value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: PRC20 balance, native balance, UTX record, outbound status, revert target, refund amount, gasless admission decision, module account nonce, ballot tally, chain meta, TSS key record, migration status, token mapping, or signature verification result.
6. Reproducible proof path: Go unit, integration, property, or fuzz-style test.

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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
