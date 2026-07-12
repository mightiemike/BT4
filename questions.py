import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
SOURCE_REPO = "crypto-org-chain/chain-main"
REPO_NAME = "chain-main"
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
    "app/ante.go",
    "app/app.go",
    "app/encoding.go",
    "app/export.go",
    "app/genesis.go",
    "app/params/encoding.go",
    "app/params/proto.go",
    "app/types.go",
    "app/upgrades.go",
    "app/versiondb.go",
    "app/versiondb_placeholder.go",
    "cmd/chain-maind/app/app.go",
    "cmd/chain-maind/app/appunix.go",
    "cmd/chain-maind/app/appwin.go",
    "cmd/chain-maind/app/versiondb.go",
    "cmd/chain-maind/app/versiondb_placeholder.go",
    "cmd/chain-maind/main.go",
    "cmd/chain-maind/opendb/opendb.go",
    "cmd/chain-maind/opendb/opendb_rocksdb.go",
    "config/config.go",
    "config/prefix_mainnet.go",
    "config/prefix_testnet.go",
    "proto/chainmain/chainmain/v1/genesis.proto",
    "proto/chainmain/inflation/v1/genesis.proto",
    "proto/chainmain/inflation/v1/params.proto",
    "proto/chainmain/inflation/v1/query.proto",
    "proto/chainmain/inflation/v1/tx.proto",
    "proto/chainmain/nft/v1/genesis.proto",
    "proto/chainmain/nft/v1/nft.proto",
    "proto/chainmain/nft/v1/query.proto",
    "proto/chainmain/nft/v1/tx.proto",
    "proto/chainmain/nft_transfer/v1/genesis.proto",
    "proto/chainmain/nft_transfer/v1/packet.proto",
    "proto/chainmain/nft_transfer/v1/query.proto",
    "proto/chainmain/nft_transfer/v1/trace.proto",
    "proto/chainmain/nft_transfer/v1/tx.proto",
    "proto/chainmain/supply/v1/accounts.proto",
    "proto/chainmain/supply/v1/genesis.proto",
    "proto/chainmain/supply/v1/query.proto",
    "proto/chainmain/tieredrewards/v1/event.proto",
    "proto/chainmain/tieredrewards/v1/genesis.proto",
    "proto/chainmain/tieredrewards/v1/params.proto",
    "proto/chainmain/tieredrewards/v1/query.proto",
    "proto/chainmain/tieredrewards/v1/tx.proto",
    "proto/chainmain/tieredrewards/v1/types.proto",
    "x/chainmain/client/cli/genaccounts.go",
    "x/chainmain/client/cli/query.go",
    "x/chainmain/client/cli/tx.go",
    "x/chainmain/client/rest/rest.go",
    "x/chainmain/genesis.go",
    "x/chainmain/keeper/keeper.go",
    "x/chainmain/module.go",
    "x/chainmain/types/errors.go",
    "x/chainmain/types/genesis.go",
    "x/chainmain/types/keys.go",
    "x/chainmain/types/types.go",
    "x/inflation/abci.go",
    "x/inflation/client/cli/query.go",
    "x/inflation/client/cli/tx.go",
    "x/inflation/keeper/genesis.go",
    "x/inflation/keeper/grpc_query.go",
    "x/inflation/keeper/keeper.go",
    "x/inflation/keeper/mint.go",
    "x/inflation/keeper/msg_server.go",
    "x/inflation/module.go",
    "x/inflation/types/codec.go",
    "x/inflation/types/expected_keepers.go",
    "x/inflation/types/genesis.go",
    "x/inflation/types/keys.go",
    "x/inflation/types/params.go",
    "x/nft-transfer/client/cli/cli.go",
    "x/nft-transfer/client/cli/query.go",
    "x/nft-transfer/client/cli/tx.go",
    "x/nft-transfer/ibc_module.go",
    "x/nft-transfer/keeper/genesis.go",
    "x/nft-transfer/keeper/grpc_query.go",
    "x/nft-transfer/keeper/keeper.go",
    "x/nft-transfer/keeper/msg_server.go",
    "x/nft-transfer/keeper/packet.go",
    "x/nft-transfer/keeper/relay.go",
    "x/nft-transfer/keeper/trace.go",
    "x/nft-transfer/module.go",
    "x/nft-transfer/types/ack.go",
    "x/nft-transfer/types/codec.go",
    "x/nft-transfer/types/errors.go",
    "x/nft-transfer/types/events.go",
    "x/nft-transfer/types/expected_keepers.go",
    "x/nft-transfer/types/genesis.go",
    "x/nft-transfer/types/keys.go",
    "x/nft-transfer/types/msgs.go",
    "x/nft-transfer/types/packet.go",
    "x/nft-transfer/types/trace.go",
    "x/nft/client/cli/flags.go",
    "x/nft/client/cli/query.go",
    "x/nft/client/cli/tx.go",
    "x/nft/exported/nft.go",
    "x/nft/genesis.go",
    "x/nft/keeper/collection.go",
    "x/nft/keeper/denom.go",
    "x/nft/keeper/grpc_query.go",
    "x/nft/keeper/keeper.go",
    "x/nft/keeper/msg_server.go",
    "x/nft/keeper/nft.go",
    "x/nft/keeper/owners.go",
    "x/nft/module.go",
    "x/nft/types/codec.go",
    "x/nft/types/collection.go",
    "x/nft/types/denom.go",
    "x/nft/types/errors.go",
    "x/nft/types/events.go",
    "x/nft/types/expected_keepers.go",
    "x/nft/types/genesis.go",
    "x/nft/types/keys.go",
    "x/nft/types/msgs.go",
    "x/nft/types/nft.go",
    "x/nft/types/owners.go",
    "x/nft/types/querier.go",
    "x/nft/types/validation.go",
    "x/supply/client/cli/query.go",
    "x/supply/keeper/genesis.go",
    "x/supply/keeper/grpc_query.go",
    "x/supply/keeper/keeper.go",
    "x/supply/module.go",
    "x/supply/types/expected_keepers.go",
    "x/supply/types/genesis.go",
    "x/supply/types/keys.go",
    "x/supply/types/querier.go",
    "x/tieredrewards/client/cli/helpers.go",
    "x/tieredrewards/client/cli/query.go",
    "x/tieredrewards/client/cli/tx.go",
    "x/tieredrewards/keeper/abci.go",
    "x/tieredrewards/keeper/bonus_rewards.go",
    "x/tieredrewards/keeper/claim_rewards.go",
    "x/tieredrewards/keeper/collections_helpers.go",
    "x/tieredrewards/keeper/delegation.go",
    "x/tieredrewards/keeper/force_exit.go",
    "x/tieredrewards/keeper/genesis.go",
    "x/tieredrewards/keeper/gov_tally.go",
    "x/tieredrewards/keeper/grpc_query.go",
    "x/tieredrewards/keeper/hooks.go",
    "x/tieredrewards/keeper/keeper.go",
    "x/tieredrewards/keeper/migrations.go",
    "x/tieredrewards/keeper/msg_server.go",
    "x/tieredrewards/keeper/msg_server_auth.go",
    "x/tieredrewards/keeper/msg_validate.go",
    "x/tieredrewards/keeper/position.go",
    "x/tieredrewards/keeper/position_state.go",
    "x/tieredrewards/keeper/redelegation_mapping.go",
    "x/tieredrewards/keeper/slash.go",
    "x/tieredrewards/keeper/tier.go",
    "x/tieredrewards/keeper/transfer_delegation.go",
    "x/tieredrewards/keeper/validator_events.go",
    "x/tieredrewards/keeper/voting_power.go",
    "x/tieredrewards/migrations/v2/migrate.go",
    "x/tieredrewards/module.go",
    "x/tieredrewards/types/codec.go",
    "x/tieredrewards/types/errors.go",
    "x/tieredrewards/types/expected_keepers.go",
    "x/tieredrewards/types/genesis.go",
    "x/tieredrewards/types/keys.go",
    "x/tieredrewards/types/msgs.go",
    "x/tieredrewards/types/params.go",
    "x/tieredrewards/types/position.go",
    "x/tieredrewards/types/position_state.go",
    "x/tieredrewards/types/tier.go",
]

target_scopes = [
    "Critical. Unprivileged on-chain action causes unintentional withdrawal, draining, loss, theft, burn, or permanent lock of user funds or economically valuable NFTs on Cronos POS Chain.",
    "Critical. Inflation, supply, bank, module-account, mint, burn, or escrow accounting flaw creates unbacked assets, loses backed assets, or lets value leave the intended module/account boundary.",
    "Critical. IBC NFT transfer escrow, burn, mint, class-trace, acknowledgement, timeout, or refund flaw enables duplicate withdrawal, unauthorized voucher minting, unauthorized unescrow, or loss of NFTs.",
    "Critical. NFT module authorization or ownership invariant break lets an attacker mint, transfer, burn, edit, or seize denominations or NFTs they do not control.",
    "Critical. Tiered rewards position, delegation, redelegation, slashing, exit, withdrawal, or reward-accounting flaw lets an attacker withdraw delegated stake, claim rewards, or move voting power not owned by them.",
    "Critical. Genesis, migration, upgrade, app wiring, keeper permission, or module account configuration flaw installs unsafe production state that can directly lead to fund loss or unauthorized asset movement.",
    "High. Reward, inflation-decay, base/bonus reward, or staking hook logic flaw lets a user repeatedly or incorrectly claim material rewards or bypass lock/exit economics with direct economic loss.",
    "High. Ante, authz, feegrant, address-prefix, signer, or CLI transaction construction flaw causes a signed or authorized production transaction to spend, lock, burn, transfer, or delegate assets contrary to the signer authorization.",
    "High. Cross-module invariant break between staking, slashing, distribution, bank, NFT, NFT-transfer, supply, inflation, or tieredrewards corrupts balances, shares, ownership, rewards, or escrow state with direct fund-loss impact.",
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one Cronos POS Chain target.

    target_file format:
    "'File Name: x/tieredrewards/keeper/msg_server.go -> Scope: Critical. Tiered rewards position, delegation, redelegation, slashing, exit, withdrawal, or reward-accounting flaw lets an attacker withdraw delegated stake, claim rewards, or move voting power not owned by them.'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit and fuzzing questions for this exact Cronos POS Chain target:

    {target_file}

    Project focus:
    This repository is Cronos POS Chain, a Cosmos SDK/CometBFT application. Production value paths include app wiring and ante handling, module account permissions, genesis/upgrades/version DB, `x/tieredrewards`, `x/nft`, `x/nft-transfer`, `x/inflation`, `x/supply`, `x/chainmain`, protobuf message definitions, and CLI transaction builders. The bounty focus is High/Critical blockchain impact: direct fund loss, draining, unauthorized asset movement, unbacked minting, duplicate claims, or economically meaningful loss of NFTs or rewards.

    Use concrete project mechanisms when relevant: `ChainApp`, `maccPerms`, `moduleAccsAllowedToReceiveExternalFunds`, keeper wiring, `MsgServer` methods, `Validate`/`ValidateBasic`, `GetSigners`, authz/feegrant/ante handling, NFT denom/owner maps, IBC NFT class traces and escrow addresses, packet ack/refund/timeout paths, tiered rewards positions, delegated accounts, slashing hooks, reward checkpoints, inflation decay, supply queries, genesis validation, and migrations.

    Analyst mindset:

    * Think like an exploit engineer, not a linter.
    * Infer the file's role first, then generate only questions that fit that role.
    * Reason in state transitions: balances, supply, module account funds, NFT ownership, denom ownership, escrowed assets, vouchers, delegation shares, reward checkpoints, position state, voting power, signer identity, and genesis or migration state before and after the exploit path.
    * Prefer questions that can produce unauthorized transfer/burn/mint, duplicate withdrawal/claim, bypassed lock/exit/slashing rule, unsafe module permissions, or corrupted accounting in the fewest realistic steps.
    * If the file is a library, proto, or CLI path, target only reachable production callers or signed transaction flows that depend on it.

    Core invariants:

    * No account or module account may transfer, burn, mint, escrow, unescrow, delegate, undelegate, or withdraw assets without the intended signer, owner, keeper authority, or IBC packet lifecycle.
    * NFT class traces, voucher class IDs, escrow addresses, denom owners, token owners, acks, timeouts, and refunds must conserve one real NFT per valid cross-chain transfer.
    * Tiered rewards positions must preserve owner, delegated account, validator, shares, lock/exit status, reward checkpoint, slash, and withdrawal invariants across all message and hook sequences.
    * Mint, inflation, supply, distribution, staking, and bank accounting must not create unbacked supply or allow value to leave intended module boundaries.
    * Genesis, migrations, upgrades, address prefixes, protobuf messages, signer extraction, authz, feegrant, and ante wiring must not create a production path to unauthorized asset movement.

    Rules:

    * Treat `File Name:` as the exact file/module.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Attacker may be an unprivileged account, NFT owner, delegator, validator/delegator using normal messages, authz grantee, feegrant user, IBC counterparty supplying packets, or CLI user signing a production transaction.
    * Do not rely on privileged governance, validator majority or 51% control, leaked keys, malicious maintainers, malicious operators or relayers without a project-side validation failure, unsupported local config, social engineering, or upstream-only Cosmos SDK/CometBFT/IBC bugs.
    * Exclude denial of service, spam, gas-only issues, liveness, best practices, dependency-only issues, harmless query/UI/CLI display bugs, and reward dilution without user-fund loss.
    * Generate 20 to 30 high-signal questions.
    * At least 70% must be multi-step flow, invariant, authorization, replay, IBC lifecycle, accounting, signer, migration, hook, or cross-module questions.
    * Every question must be testable by a runnable `go test`, module keeper test, app integration test, fuzz test, invariant test, or transaction-sequence PoC.
    * Avoid generic checklist questions and repeated root causes.
    * Each question must target a plausible issue class for the exact file and scope.
    * Each question must anchor to concrete symbols when possible: function names, structs, mappings, storage variables, or specific cross-module call sites.
    * Prefer questions that name the exact value that may be corrupted: account balances, supply, module account balances, NFT owner, denom owner, class trace, voucher class ID, escrow address, position ID, delegated account, delegation shares, reward checkpoints, completion time, signer, grantee, or genesis params.
    * At least half of the questions should require tracing across 2 or more modules or 2 or more functions in sequence.
    * Do not waste slots on vague prompts such as "can math break?" without a concrete value path and fund-loss invariant.
    * For arithmetic ideas, focus on `sdk.Int`, `math.LegacyDec`, shares, rewards, supply, inflation decay, rounding, zero/max amounts, completion heights/times, and overflow/underflow around casts.
    * For ordering ideas, focus on attacker-controlled sequencing of lock, add, redelegate, undelegate, trigger exit, clear, withdraw, claim, slash, hook, ack, timeout, refund, migration, or upgrade state.

    High-value attack surfaces:

    * `x/tieredrewards/keeper/*`: position lifecycle, delegated accounts, slashing hooks, redelegation mapping, rewards, bonus checkpoints, authz, voting power, migrations.
    * `x/nft-transfer/*`: IBC NFT transfer, class trace hashing, escrow/burn/mint, packet relay, ack, timeout, refund, source/sink direction.
    * `x/nft/*`: denom issue, mint, burn, transfer, edit, owners and collection indexes, validation and keeper authority.
    * `x/inflation`, `x/supply`, `app/*`: inflation decay, mint params, supply/account queries, app module permissions, blocked module accounts, ante/authz/feegrant, genesis and upgrades.
    * `proto/*` and `client/cli/*`: message fields and transaction construction only when they can cause a signed production tx or module call to move assets incorrectly.

    Impact mapping:

    * High/Critical only: direct user fund or NFT loss, unauthorized asset movement, unbacked minting, duplicate withdrawal or claim, corrupted escrow/backing, or unsafe production state that can lead to those outcomes.

    Question quality bar:

    * A strong question names the actor, entrypoint, manipulated state, missing or bypassed check, and concrete bad outcome.
    * A weak question is generic, single-function-only without impact, or does not identify what exact invariant fails.
    * Prefer one sharp question about a realistic exploit chain over several vague variants of the same bug class.

    Each question must include:

    1. target function/module;
    2. attacker action;
    3. preconditions;
    4. call sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_module] Can an attacker ACTION under PRECONDITIONS trigger CALL_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: run a Go unit, keeper, app integration, fuzz, invariant, or transaction-sequence test over PARAMETERS and assert EXPECTED_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Cronos POS Chain exploit-question validation prompt.
    """
    return f"""# QUESTION SCAN PROMPT

## Exploit Question
{question}

## Scope Rules
- Audit only production Cronos POS Chain repository code listed in `scope_files`.
- Do not ask for repo contents or claim files are missing.
- Ignore tests, docs, mocks, generated files, simulations, repo automation scripts, build files, deployment-only files, and local tooling.

## Objective
Decide whether the question leads to a real, reachable Cronos POS Chain vulnerability.
The attacker must enter through a supported production path: Cosmos SDK transaction, module `MsgServer`, authz/feegrant flow, IBC NFT packet lifecycle, genesis/migration/upgrade path with attacker-relevant state, CLI-signed transaction construction, or another externally reachable transaction or data-validation path.
The impact must match the provided target scope.
Prefer #NoVulnerability unless the path is concrete, locally testable on an unmodified Go/Cosmos test setup, and proves one of the High/Critical impacts in `target_scopes`.
Treat the question as a hypothesis that must survive adversarial review. Look for the exact balance, supply, NFT ownership, escrow, position, delegation, reward, signer, or module-permission change that would make the exploit real.

## Method
1. Trace the attacker-controlled entrypoint.
2. Map it to exact production repository files and functions.
3. Check relevant guards: `Validate`/`ValidateBasic`, `GetSigners`, keeper authority, module account permissions, blocked receives, owner checks, authz/feegrant restrictions, IBC ack/timeout/refund validation, class trace direction, staking hooks, slash handling, reward checkpoints, genesis validation, and migration guards.
4. Identify the exact state variables, balances, ownership records, reward values, or cross-module assumptions that must change for the exploit to work.
5. Decide whether the questioned invariant can actually break under intended deployment.
6. Prove root cause with file, function, and line references.
7. Confirm realistic likelihood and exact scoped impact.
8. Reject if current validation already prevents the exploit.

## Reject Immediately
- Requires governance or privileged-role control, operator compromise, code-deployer compromise, leaked private keys, malicious maintainer, unsupported local configuration, or social engineering.
- Only affects tests, docs, scripts, mocks, generated code, simulations, local tooling, or deployment choices.
- External dependency or known upstream Cosmos SDK/CometBFT/IBC behavior is the only cause.
- Impact is denial of service, spam, gas griefing, performance degradation, harmless revert behavior, logging noise, observability only, reward dilution without fund loss, or theoretical risk.
- No concrete scoped impact or no realistic exploit path.
- No exact balance, supply, owner, escrow, position, delegation, reward, signer, or module-permission delta can be named.
- The question depends on impossible chain behavior or privileges not granted by the scoped code path.

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
    Generate a short cross-project analog scan prompt for the Cronos POS Chain repository.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Access Rules (Strict)
- Treat production Cronos POS Chain repository files in the provided scope as accessible context.
- Do not claim missing or inaccessible files.
- Do not ask for repository contents.
- Do not scan tests, docs, build files, IDE files, generated files, simulations, repo automation scripts, local tooling, or deployment-only choices as audited targets.

## Objective
Use the external report's vulnerability class as a hint to find valid issues based on this repository's security impact.
Focus on externally reachable issues triggered by an unprivileged account, normal Cosmos SDK transaction, authz/feegrant flow, IBC NFT packet lifecycle, NFT owner/denom actor, delegator/validator action, CLI-signed tx path, or another supported production entrypoint.
Only report an analog if this repository has its own reachable root cause and the impact matches the provided target scope.
Be strict about analog quality: similarity of bug class is not enough. This repository must have its own concrete trigger, broken invariant, and scoped impact.

## Method
1. Classify vuln type: unauthorized asset movement, unbacked minting, duplicate claim/withdrawal, escrow/refund/ack mismatch, NFT ownership bypass, signer/authz/feegrant bypass, tiered rewards accounting break, unsafe genesis/migration, module-permission flaw, or cross-module desynchronization.
2. Map to exact production files and modules.
3. Identify the exact balance, supply, NFT owner, class trace, escrow, position, delegation shares, reward, signer, or module-account value that the analog would corrupt.
4. Prove root cause with exact file, function, module, and line references.
5. Confirm concrete scoped impact and realistic likelihood.
6. Explain the attacker-controlled entry path and why repository code is a necessary vulnerable step.
7. Reject if the impact does not match the provided target scope.

## Disqualify Immediately
- No reachable attacker-controlled entry path.
- Requires privileged-role control, leaked private keys, malicious maintainer, unsupported local configuration, or social engineering.
- External dependency or known upstream Cosmos SDK/CometBFT/IBC behavior is the only cause.
- Test, docs, build, generated, simulation, or local-tooling issue.
- Theoretical-only issue with no protocol impact.
- Impact is denial of service, spam, gas or performance-only degradation, local misconfiguration, observability noise, harmless rejection, reward dilution without user fund loss, or non-security correctness.
- Impact or likelihood missing.
- No exact corrupted balance, supply, owner, escrow, class trace, reward, delegation, signer, module-permission, or authorization assumption can be identified.

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
    Generate a strict Cronos POS Chain validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Validate against this repository's production Cronos POS Chain scope and the allowed impact classes below.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Reject governance-only, privileged-role-only, validator-majority-only, leaked-key, best-practice, docs or style, gas-only, denial-of-service, spam, performance-only, griefing-only, dependency-only, known upstream-only, and purely theoretical issues.
- Reject if the exploit requires unrealistic assumptions, victim mistakes, missing external context, or unsupported protocol behavior.
- A valid report must be triggerable through a normal Cosmos SDK transaction, module message, authz/feegrant flow, IBC NFT packet lifecycle, attacker-relevant genesis/migration state, CLI-signed transaction path, or another supported production entrypoint.
- The final impact must match one of the High/Critical `target_scopes`, not just a generic code bug.
- Prefer #NoVulnerability over speculative reports.
- Be skeptical of reports that describe a bug class without naming the exact balance, supply, ownership, escrow, position, delegation, reward, signer, or permission change produced by the exploit.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unprivileged on-chain action causes unintentional withdrawal, draining, loss, theft, burn, or permanent lock of user funds or economically valuable NFTs on Cronos POS Chain.
- Critical. Inflation, supply, bank, module-account, mint, burn, or escrow accounting flaw creates unbacked assets, loses backed assets, or lets value leave the intended module/account boundary.
- Critical. IBC NFT transfer escrow, burn, mint, class-trace, acknowledgement, timeout, or refund flaw enables duplicate withdrawal, unauthorized voucher minting, unauthorized unescrow, or loss of NFTs.
- Critical. NFT module authorization or ownership invariant break lets an attacker mint, transfer, burn, edit, or seize denominations or NFTs they do not control.
- Critical. Tiered rewards position, delegation, redelegation, slashing, exit, withdrawal, or reward-accounting flaw lets an attacker withdraw delegated stake, claim rewards, or move voting power not owned by them.
- Critical. Genesis, migration, upgrade, app wiring, keeper permission, or module account configuration flaw installs unsafe production state that can directly lead to fund loss or unauthorized asset movement.
- High. Reward, inflation-decay, base/bonus reward, or staking hook logic flaw lets a user repeatedly or incorrectly claim material rewards or bypass lock/exit economics with direct economic loss.
- High. Ante, authz, feegrant, address-prefix, signer, or CLI transaction construction flaw causes a signed or authorized production transaction to spend, lock, burn, transfer, or delegate assets contrary to the signer authorization.
- High. Cross-module invariant break between staking, slashing, distribution, bank, NFT, NFT-transfer, supply, inflation, or tieredrewards corrupts balances, shares, ownership, rewards, or escrow state with direct fund-loss impact.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line or code references.
2. Clear root cause and broken protocol, authorization, accounting, ownership, escrow, signer, or module-permission assumption.
3. Reachable exploit path: preconditions -> attacker action -> trigger -> bad result.
4. Existing checks or guards reviewed and shown insufficient.
5. Exact corrupted state or value delta identified: what balance, supply, NFT owner, class trace, escrow, position, delegation shares, reward checkpoint, signer, role boundary, or config value changed incorrectly.
6. Concrete impact that exactly matches one allowed repository impact above, with realistic likelihood.
7. Reproducible proof path: Go unit test, keeper test, app integration test, transaction sequence, fuzz or invariant harness, or a justified local reproducer.
8. No obvious rejection reason from privileges, assumptions, known behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can a normal external user or recipient contract trigger this?
- Does the code actually behave as claimed?
- Is the impact caused by this protocol, not by an external dependency alone?
- Is the protocol-state impact concrete, not hypothetical?
- What exact balances, supply values, NFT ownership records, escrow records, position fields, reward values, or signed transaction fields are wrong after the exploit?
- What accounting equation, ownership rule, IBC lifecycle rule, signer rule, staking/reward rule, or authorization rule is broken?
- Would a security triager accept the proof?
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
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or fuzz, invariant, or state test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
