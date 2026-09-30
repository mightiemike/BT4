import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 10
# todo: the path from https://github.com/0xPolygon/heimdall-v2
SOURCE_REPO = "0xPolygon/heimdall-v2"
# todo: the name of the repository
REPO_NAME = "heimdall-v2"
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
    # ABCI++ core: PrepareProposal/ProcessProposal/ExtendVote/VerifyVoteExtension/PreBlocker,
    # vote-extension tallying, tx decode guard, ante chain, pending-stall handling
    # =================================================================================
    "app/abci.go",
    "app/vote_ext_utils.go",
    "app/ante.go",
    "app/tx_decode_guard.go",
    "app/pending_stall.go",
    "app/bor_failover_guard.go",
    "app/app.go",
    "app/util.go",

    # =================================================================================
    # side-tx pipeline: side/post handler registry, side-tx ante decorator
    # =================================================================================
    "sidetxs/side_handler.go",
    "sidetxs/side_tx_configurator.go",
    "sidetxs/ante_decorator.go",

    # =================================================================================
    # x/stake: validator join/exit/stake-update/signer-update from L1 events, nonce checks,
    # validator set and voting power
    # =================================================================================
    "x/stake/keeper/msg_server.go",
    "x/stake/keeper/side_msg_server.go",
    "x/stake/keeper/keeper.go",
    "x/stake/keeper/validator.go",
    "x/stake/keeper/abci.go",
    "x/stake/types/msg.go",
    "x/stake/types/validator.go",
    "x/stake/types/validator_set.go",
    "x/stake/types/side_tx.go",

    # =================================================================================
    # x/checkpoint: checkpoint proposal, L1 root verification, ack, buffer, merkle root,
    # account root hash
    # =================================================================================
    "x/checkpoint/keeper/msg_server.go",
    "x/checkpoint/keeper/side_msg_server.go",
    "x/checkpoint/keeper/keeper.go",
    "x/checkpoint/types/msg.go",
    "x/checkpoint/types/checkpoint.go",
    "x/checkpoint/types/merkle.go",
    "x/checkpoint/types/side_tx.go",
    "x/checkpoint/ante/account_root_hash_len.go",

    # =================================================================================
    # x/topup: fee top-up from L1, fee withdrawal, dividend accounts
    # =================================================================================
    "x/topup/keeper/msg_server.go",
    "x/topup/keeper/side_msg_server.go",
    "x/topup/keeper/keeper.go",
    "x/topup/types/msg.go",
    "x/topup/types/side_tx.go",
    "types/dividend_account.go",

    # =================================================================================
    # x/clerk: state-sync records from L1 StateSender events
    # =================================================================================
    "x/clerk/keeper/msg_server.go",
    "x/clerk/keeper/side_msg_server.go",
    "x/clerk/keeper/keeper.go",
    "x/clerk/types/msg.go",
    "x/clerk/types/record.go",
    "x/clerk/types/side_tx.go",

    # =================================================================================
    # x/bor: span proposal, producer selection, VEBLOP, producer fallback
    # =================================================================================
    "x/bor/keeper/msg_server.go",
    "x/bor/keeper/side_msg_server.go",
    "x/bor/keeper/keeper.go",
    "x/bor/keeper/selection.go",
    "x/bor/keeper/veblop.go",
    "x/bor/keeper/veblop_producer_fallback.go",
    "x/bor/types/msg.go",
    "x/bor/types/side_tx.go",
    "x/bor/types/params.go",
    "x/bor/types/util.go",

    # =================================================================================
    # x/milestone: milestone proposal/validation and ABCI hooks; x/chainmanager: chain params
    # =================================================================================
    "x/milestone/keeper/msg_server.go",
    "x/milestone/keeper/keeper.go",
    "x/milestone/abci/abci.go",
    "x/milestone/types/milestone.go",
    "x/chainmanager/keeper/msg_server.go",
    "x/chainmanager/keeper/keeper.go",
    "x/chainmanager/keeper/abci.go",
    "x/chainmanager/types/params.go",

    # =================================================================================
    # helper: L1 receipt/log validation, contract calls, tx building/signing, sanitizing,
    # sequence (tx hash + log index) encoding, config heights
    # =================================================================================
    "helper/call.go",
    "helper/receipt.go",
    "helper/sequence.go",
    "helper/tx.go",
    "helper/validation.go",
    "helper/sanitize.go",
    "helper/unpack.go",
    "helper/util.go",
    "helper/messages.go",
    "helper/query.go",
    "helper/config.go",

    # =================================================================================
    # bridge: L1/Heimdall/Bor event listeners and processors that turn public L1 events
    # into Heimdall txs
    # =================================================================================
    "bridge/listener/rootchain.go",
    "bridge/listener/rootchain_log.go",
    "bridge/listener/rootchain_selfheal.go",
    "bridge/listener/rootchain_selfheal_graph.go",
    "bridge/listener/borchain.go",
    "bridge/listener/heimdall.go",
    "bridge/listener/base.go",
    "bridge/processor/stake.go",
    "bridge/processor/checkpoint.go",
    "bridge/processor/clerk.go",
    "bridge/processor/topup_fee.go",
    "bridge/processor/span.go",
    "bridge/processor/base.go",
    "bridge/util/common.go",
    "bridge/util/db.go",

    # =================================================================================
    # shared types: event keys, error handling, proof REST helpers
    # =================================================================================
    "types/events.go",
    "types/keys.go",
    "types/rest/proof.go",
]


target_scopes = [
    "Critical. Forged L1 events mint stake, fees or state syncs: an unprivileged tx submitter or L1 event emitter causes Heimdall to accept a stake join/update, fee top-up or clerk state-sync record that never happened on L1 or has other values (amount, signer, user, data) - because helper/call.go and receipt.go log selection (log index, contract address, event topic, removed/reorg, confirmations), the side_msg_server.go handlers of stake/topup/clerk (SideHandleMsg* comparing msg fields against the receipt) or bridge/processor/*.go fail to bind every field of the tx to the verified L1 log.",
    "Critical. Replay / double-processing of a real L1 event: the same StakeManager, StateSender or top-up log is credited twice or under two encodings - via stake nonce checks in x/stake side_msg_server.go and msg_server.go, topup tx-hash + log-index sequence keys (helper/sequence.go, x/topup keeper), clerk record id / sequence, hex-case or padding variants of TxHash, or PostHandle state written before the side-tx result is final - causing duplicated voting power, duplicated fee balance or duplicated state-sync.",
    "Critical. Checkpoint forgery or theft through the bridge: a crafted MsgCheckpoint / MsgCpAck (root hash, start/end block, proposer, account root hash, checkpoint number, buffer state) or the accountroot/merkle code in x/checkpoint gets accepted so Heimdall signs and submits a root that does not match Bor's real chain or that L1 RootChain accepts for a wrong range - enabling exits (withdrawals) against a fake root, i.e. loss of bridge funds.",
    "Critical. Theft or inflation of user fees: MsgWithdrawFee, MsgTopupTx and dividend account handling in x/topup (balance math, receiver address, amount, fee deduction in app/ante.go, negative or overflowing math.Int, duplicated dividend entries, withdraw-then-topup ordering) let an unprivileged user withdraw more than deposited, credit funds to another account, mint spendable balance, or drain the fee pool.",
    "Critical. Validator-set takeover by an unprivileged L1 staker: MsgValidatorJoin, MsgStakeUpdate, MsgSignerUpdate and MsgValidatorExit handling in x/stake (pubkey / signer validation and uniqueness, signer-address reuse, power calculation, exit and unbond ordering, validator set update in keeper.go / abci.go, validator_set.go proposer priority and total-power math) lets a minimal-stake actor gain signer control of another validator, inflate power, or push the set past the 2/3 threshold used for checkpoint signatures, endangering staking and bridge funds.",
    "High. Consensus halt from one unprivileged tx: a crafted transaction (malformed Any, nil or oversized fields, duplicate signers, side-tx message count, non-canonical bytes, bad address/hash lengths) reaches app/tx_decode_guard.go, sidetxs/ante_decorator.go, ValidateBasic in x/*/types/msg.go, PrepareProposal or ProcessProposal and causes a panic, error or nondeterministic result on every honest validator, so blocks cannot be proposed or accepted (total network shutdown or transient consensus failure).",
    "High. Poison-pill side-tx or L1 event: a valid but adversarial L1 event (StateSender data, stake or top-up values, zero or max amounts, unusual addresses) makes a side handler or PostHandleMsg in x/clerk / x/stake / x/topup / x/checkpoint return an error or diverge across validators, stalling the pending side-tx queue, PreBlocker or vote-extension tally (app/pending_stall.go, app/vote_ext_utils.go, app/abci.go) so all later checkpoints, state syncs and stake updates stop.",
    "High. Span, producer and milestone corruption: an unprivileged actor's stake or tx causes x/bor (MsgProposeSpan, selection.go, veblop.go, producer fallback, span boundaries, seed and ordering that must be deterministic) or x/milestone (MsgMilestone, hash / block-range validation, milestone ABCI) to accept a span or milestone that gives Bor a wrong producer set, a gap or overlap, or a false finality point - causing Bor to halt, reorg or finalize the wrong chain.",
    "High. Permanent or temporary freezing of funds/flows: a state transition reachable by an unprivileged user (checkpoint buffer never cleared or ack rejected forever, exit/unbond stuck, topup withdrawal always failing, span or milestone counters not advancing, clerk sequence gap, bridge processor/listener self-heal loop that skips or re-queues events wrongly) blocks checkpoints, withdrawals, stake changes or state sync until a hardfork or long outage.",
    "Critical/High blind spot. Something the protocol never considered: an unprivileged tx or L1 event exploits a mismatch between components - hardfork-height gating differing between CheckTx, PrepareProposal, ProcessProposal and PreBlocker; side-tx result vs PostHandle state divergence; genesis/export/migration state that breaks invariants; Heimdall-Bor gRPC/HTTP responses trusted in deterministic paths; L1 reorg or finality assumptions in helper/call.go; math.Int / uint64 truncation between proto, keeper and L1 units; module-account or fee accounting invariants across modules - yielding fund loss, forged state, frozen funds or a chain halt.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit questions for one Heimdall v2 target.

    ```
    target_file format:
    "'File Name: x/topup/keeper/side_msg_server.go -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact Heimdall v2 target:

    {target_file}

    Project focus:
    Heimdall v2 is the Polygon PoS consensus layer (Cosmos SDK + CometBFT, ABCI++). L1 events (StakeManager, StateSender, RootChain) become Heimdall txs via the bridge, are verified by validators through vote-extension side txs (SideHandleMsg / PostHandleMsg), and drive validator stake, fee top-ups, state syncs, checkpoints (bridge exits), spans and milestones. Bounty (Immunefi): Critical = direct loss/theft of funds, protocol insolvency, loss of bridge or staking funds; High = permanent/temporary freezing of funds, theft of user fees, transient consensus failure, total network shutdown; Medium = DoS, freezing under 1 week.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible. Do not ask for code or say anything is missing.
    * Use exact Go symbols (package, type, func, msg, keeper method, store key) when possible.
    * Attacker is unprivileged: holds no validator key, no governance/authority rights, no operator or RPC access. They can only (a) submit signed txs through public RPC/mempool, (b) emit or trigger real L1 events as a normal staker/depositor/user (join, stake, top up, StateSender sync, initiate exit), (c) send public queries.
    * Never assume a malicious validator, proposer, peer, node, RPC provider, Bor operator, leaked key, governance/authority action, 51% or Sybil attack, misconfiguration, test code, or an unmodified upstream Cosmos SDK / CometBFT bug.
    * Out of scope: tests, mocks, generated code, docs, migration CLI tooling, and DoS by traffic volume, unbounded loops, memory growth or huge inputs.
    * Every question must be a real-world scenario: name the tx or L1 event, the exact crafted field values, the chain state it relies on, the broken invariant, and the resulting funds or liveness impact. Follow a valid entry point: tx -> CheckTx/ante -> PrepareProposal/ProcessProposal -> ExtendVote/VerifyVoteExtension -> PreBlocker -> Side/Post handler -> keeper state, or L1 log -> bridge listener/processor -> tx.
    * Generate 40 to 80 high-signal questions. At least 70% must target Critical or High impact from the Scope.
    * Every question must be testable with a Go unit or keeper test in the target package.
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * L1 fidelity: state changes from L1 events match exactly one real, final L1 log and its values.
    * Replay safety: each L1 event, nonce and sequence is applied at most once.
    * Fund conservation: fee balances, dividends, voting power and stake never exceed what L1 backs.
    * Determinism: all honest validators reach the same result from the same block and vote extensions.
    * Liveness: no single unprivileged tx or event blocks proposals, checkpoints, spans, milestones or the side-tx queue.
    * Checkpoint integrity: only a root matching Bor's real chain and range is signed and accepted.

    Each question must include:
    1. target function/method;
    2. attacker action (tx or L1 event with crafted fields);
    3. preconditions (validator set, nonce/sequence, height, buffer state);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Can an unprivileged ATTACKER_INPUT under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: go test PARAMETERS and assert L1_FIDELITY, REPLAY_SAFETY, FUND_CONSERVATION, DETERMINISM, LIVENESS, or CHECKPOINT_INTEGRITY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused Heimdall v2 exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged: no validator key, no governance/authority rights, no operator or RPC access. They can only submit txs via public RPC, emit or trigger real L1 events as a normal user or staker, and send public queries.
- Reject malicious-validator/proposer/peer/node, malicious-RPC, Bor-operator, leaked-key, governance/authority, 51%/Sybil, misconfiguration, test/mock/generated code, and unmodified upstream Cosmos SDK / CometBFT bug paths.
- Reject generic unbounded-loop, memory or traffic-volume DoS claims with no concrete input and no broken invariant.
- Focus on real impact: loss or theft of funds, bridge/staking fund loss, insolvency, fee theft, freezing of funds, transient consensus failure or total network shutdown.

## Validate
- Trace the exact path from the attacker's tx or L1 event through CheckTx/ante, PrepareProposal/ProcessProposal, vote extensions, PreBlocker, side/post handlers and keeper state (or bridge listener -> processor -> tx).
- Check existing guards: ValidateBasic, tx_decode_guard, ante decorators, receipt and log validation in helper/call.go, nonce and sequence (tx hash + log index) checks, side-tx result tallying thresholds, hardfork height gates, and keeper invariants.
- Accept only a concrete, reachable exploit with exact file/function support and a reproducible `go test` PoC.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker input, exploit flow, and why existing guards fail]

### Impact Explanation
[Concrete scoped impact and severity: Critical (direct loss/theft of funds, insolvency, loss of bridge or staking funds), High (freezing of funds, fee theft, transient consensus failure, network shutdown) or Medium (DoS, freezing under 1 week)]

### Likelihood Explanation
[Attacker capability, required inputs and state, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[go test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for Heimdall v2.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production code only: app/, sidetxs/, x/{{stake,checkpoint,topup,clerk,bor,milestone,chainmanager}} (keeper, types, ante, abci), helper/ (call, receipt, sequence, tx, validation, sanitize, config), bridge/ (listener, processor, util), types/. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof. The analog must stand on Heimdall's own code.
- Keep only analogs an unprivileged party can reach: a signed tx via public RPC, a real L1 event emitted as a normal staker/depositor/user, or a public query.
- Map the class onto Heimdall's real shape, where its bugs live:
  * L1 event trust: receipt/log selection, contract address and topic checks, log index, confirmations and reorgs, msg fields not bound to the verified log (helper/call.go, receipt.go, side_msg_server.go);
  * replay and ordering: stake nonces, tx-hash + log-index sequence keys, clerk ids, hex-case variants, state written before side-tx approval;
  * fund math: math.Int / uint64 / big.Int conversions, wei vs token units, fee deduction in ante, dividend accounts, withdraw-fee balance, negative or zero amounts, module-account conservation;
  * validator set: pubkey and signer validation and uniqueness, power updates, exit/unbond ordering, proposer priority, 2/3 thresholds;
  * ABCI++ determinism and liveness: panics or errors in PrepareProposal/ProcessProposal/ExtendVote/VerifyVoteExtension/PreBlocker, nil proto fields, Any unpacking, map iteration, time or RPC in deterministic paths, hardfork height gates (off-by-one, zero semantics);
  * checkpoint and bridge: root hash / account root hash / merkle math, start/end range, ack and buffer state, exit proofs;
  * span/milestone: producer selection, VEBLOP fallback, span boundaries, milestone hash and range validation;
  * stuck state: poison-pill events, counters that never advance, queues that never drain, self-heal loops.
- Reject malicious-validator/proposer/peer/node, malicious-RPC, Bor-operator, leaked-key, governance/authority, 51%/Sybil, misconfiguration, upstream-only, test-only, volume-based DoS, and no-impact analogs.
- Critical, High and Medium only; no low, informational or best-practice analogs.

## Validate
- Map the bug class to the strongest reachable path from a tx or L1 event, naming the exact functions and field values.
- Prove root cause with exact file/function support.
- Accept only concrete loss or theft of funds, bridge/staking fund loss, fee theft, freezing of funds, consensus failure, network shutdown, or DoS/temporary freezing under 1 week.

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
    Generate a strict bounty-style validation prompt for Heimdall v2 security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and RESEARCHER.md for scope, exclusions, and valid impact classes.
- Scope (Immunefi Polygon program): Polygon PoS Heimdall (Cosmos SDK / CometBFT based) production code. Bor, contracts and other assets are out of scope for this repo.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Accepted impacts only:
  * Critical: direct loss of funds; direct theft of any user funds (at-rest or in-motion, excluding unclaimed yield); protocol insolvency; loss of bridge or staking funds.
  * High: permanent freezing of funds (requiring hardfork); temporary freezing of funds; theft of user fees; transient consensus failures; network not being able to confirm new transactions (total network shutdown).
  * Medium: denial of service; temporary freezing of funds for less than 1 week.
- Reject unmodified upstream dependency bugs, previously known Ethereum/Tendermint/Cosmos-SDK issues, basic economic attacks (51%, Sybil), centralization risks, privileged access (validators, governance/authority, operators), leaked keys, malicious peers/nodes/RPC, best-practice critiques, self-exploited damage, phishing/social engineering, test/config/mock/generated code, volume-based DoS, and anything tested on mainnet or a public testnet.
- A PoC is mandatory for every severity; prose alone is not accepted. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. Clear root cause and a broken L1-fidelity, replay-safety, fund-conservation, determinism, liveness or checkpoint-integrity invariant.
3. Reachable path from an unprivileged actor (public tx, real L1 event as a normal user/staker, or public query) through the real entry point (CheckTx/ante -> proposal handlers -> vote extensions -> PreBlocker -> side/post handler -> keeper, or bridge -> tx), with no validator, authority or operator rights.
4. Existing guards reviewed and shown insufficient: ValidateBasic, tx decode guard, ante decorators, receipt/log validation, nonce and sequence checks, vote-extension thresholds, hardfork height gates, keeper invariants.
5. Concrete impact matching one accepted category above, with realistic likelihood.
6. Reproducible proof path: a `go test` PoC (keeper, ABCI or integration test) on a local setup.
7. No rejection reason from SECURITY.md, privilege assumptions, or known issues.

## Silent Triage Questions
Before output, internally answer:
- Can a normal user, staker or depositor trigger this with public inputs only?
- Does the code actually behave as claimed on the real ABCI++/bridge path, not only in an isolated unit?
- Is the impact in Heimdall itself, not in upstream Cosmos SDK / CometBFT, Bor, contracts, or a malicious validator/node?
- Is it absent from known issues and the audits folder?
- Is the impact concrete (funds, freezing, consensus failure) rather than hypothetical?
- Would a triager accept the proof-of-concept, and what exact test proves it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[Concrete in-scope impact, severity rationale, and the exact Immunefi Polygon impact it maps to]

## Likelihood Explanation
[Attacker capability, inputs and state required, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or a go test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
