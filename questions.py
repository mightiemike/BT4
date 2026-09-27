import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 12
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'rocket-pool/rocketpool'
# todo: the name of the repository
REPO_NAME = 'rocketpool'

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
    # Megapool: validator lifecycle, capital/bond/debt accounting, delegate proxy
    # =================================================================================
    "contracts/contract/megapool/RocketMegapoolDelegate.sol",
    "contracts/contract/megapool/RocketMegapoolDelegateBase.sol",
    "contracts/contract/megapool/RocketMegapoolProxy.sol",
    "contracts/contract/megapool/RocketMegapoolStorageLayout.sol",
    "contracts/contract/megapool/RocketMegapoolFactory.sol",
    "contracts/contract/megapool/RocketMegapoolManager.sol",
    "contracts/contract/megapool/RocketMegapoolPenalties.sol",

    # =================================================================================
    # Beacon state proofs: SSZ merkleisation, EIP-4788 roots, validator/withdrawal/slot proofs
    # =================================================================================
    "contracts/contract/util/BeaconStateVerifier.sol",
    "contracts/contract/util/SSZ.sol",

    # =================================================================================
    # Deposit pool, assignment queues, rETH and vault
    # =================================================================================
    "contracts/contract/deposit/RocketDepositPool.sol",
    "contracts/contract/util/LinkedListStorage.sol",
    "contracts/contract/token/RocketTokenRETH.sol",
    "contracts/contract/RocketVault.sol",

    # =================================================================================
    # Node operators: registration, deposits, credit, RPL staking, withdrawal addresses
    # =================================================================================
    "contracts/contract/node/RocketNodeManager.sol",
    "contracts/contract/node/RocketNodeDeposit.sol",
    "contracts/contract/node/RocketNodeStaking.sol",
    "contracts/contract/node/RocketNodeDistributor.sol",
    "contracts/contract/node/RocketNodeDistributorDelegate.sol",
    "contracts/contract/node/RocketNodeDistributorFactory.sol",
    "contracts/contract/node/RocketNodeDistributorStorageLayout.sol",

    # =================================================================================
    # Legacy minipools: distribution, bond reduction, queue, penalties
    # =================================================================================
    "contracts/contract/minipool/RocketMinipoolBase.sol",
    "contracts/contract/minipool/RocketMinipoolDelegate.sol",
    "contracts/contract/minipool/RocketMinipoolStorageLayout.sol",
    "contracts/contract/minipool/RocketMinipoolFactory.sol",
    "contracts/contract/minipool/RocketMinipoolManager.sol",
    "contracts/contract/minipool/RocketMinipoolQueue.sol",
    "contracts/contract/minipool/RocketMinipoolBondReducer.sol",
    "contracts/contract/minipool/RocketMinipoolPenalty.sol",

    # =================================================================================
    # Rewards: merkle claims, rewards pool, smoothing pool, pDAO treasury
    # =================================================================================
    "contracts/contract/rewards/RocketMerkleDistributorMainnet.sol",
    "contracts/contract/rewards/RocketRewardsPool.sol",
    "contracts/contract/rewards/RocketSmoothingPool.sol",
    "contracts/contract/rewards/RocketClaimDAO.sol",

    # =================================================================================
    # Network: balances, prices, fees, revenue split, snapshots, penalties, voting power
    # =================================================================================
    "contracts/contract/network/RocketNetworkBalances.sol",
    "contracts/contract/network/RocketNetworkPrices.sol",
    "contracts/contract/network/RocketNetworkFees.sol",
    "contracts/contract/network/RocketNetworkRevenues.sol",
    "contracts/contract/network/RocketNetworkSnapshots.sol",
    "contracts/contract/network/RocketNetworkSnapshotsTime.sol",
    "contracts/contract/network/RocketNetworkPenalties.sol",
    "contracts/contract/network/RocketNetworkVoting.sol",

    # =================================================================================
    # Protocol DAO: proposals, voting-power verifier (challenge/response bonds), settings
    # =================================================================================
    "contracts/contract/dao/RocketDAOProposal.sol",
    "contracts/contract/dao/protocol/RocketDAOProtocol.sol",
    "contracts/contract/dao/protocol/RocketDAOProtocolActions.sol",
    "contracts/contract/dao/protocol/RocketDAOProtocolProposal.sol",
    "contracts/contract/dao/protocol/RocketDAOProtocolProposals.sol",
    "contracts/contract/dao/protocol/RocketDAOProtocolVerifier.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettings.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsAuction.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsDeposit.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsInflation.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsMegapool.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsMinipool.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsNetwork.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsNode.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsProposals.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsRewards.sol",
    "contracts/contract/dao/protocol/settings/RocketDAOProtocolSettingsSecurity.sol",

    # =================================================================================
    # Oracle DAO and security council: membership, proposals, upgrades, settings
    # =================================================================================
    "contracts/contract/dao/node/RocketDAONodeTrusted.sol",
    "contracts/contract/dao/node/RocketDAONodeTrustedActions.sol",
    "contracts/contract/dao/node/RocketDAONodeTrustedProposals.sol",
    "contracts/contract/dao/node/RocketDAONodeTrustedUpgrade.sol",
    "contracts/contract/dao/node/settings/RocketDAONodeTrustedSettings.sol",
    "contracts/contract/dao/node/settings/RocketDAONodeTrustedSettingsMembers.sol",
    "contracts/contract/dao/node/settings/RocketDAONodeTrustedSettingsMinipool.sol",
    "contracts/contract/dao/node/settings/RocketDAONodeTrustedSettingsProposals.sol",
    "contracts/contract/dao/node/settings/RocketDAONodeTrustedSettingsRewards.sol",
    "contracts/contract/dao/security/RocketDAOSecurity.sol",
    "contracts/contract/dao/security/RocketDAOSecurityActions.sol",
    "contracts/contract/dao/security/RocketDAOSecurityProposals.sol",
    "contracts/contract/dao/security/RocketDAOSecurityUpgrade.sol",

    # =================================================================================
    # Core storage, RPL token, auction and shared utilities
    # =================================================================================
    "contracts/contract/RocketStorage.sol",
    "contracts/contract/RocketBase.sol",
    "contracts/contract/token/RocketTokenRPL.sol",
    "contracts/contract/auction/RocketAuctionManager.sol",
    "contracts/contract/util/AddressQueueStorage.sol",
    "contracts/contract/util/AddressSetStorage.sol",
    "contracts/contract/util/ERC20.sol",
    "contracts/contract/util/ERC20Burnable.sol",
    "contracts/contract/util/SafeERC20.sol",
    "contracts/contract/util/SafeMath.sol",
    "contracts/contract/util/Context.sol",
]


target_scopes = [
    "Critical. Anyone submitting beacon-state proofs gets a false validator or withdrawal state accepted, because BeaconStateVerifier.verifyValidator/verifyWithdrawal/verifySlot, its generalized-index and fork-boundary handling, the historical_summaries vs block_roots path, EIP-4788 root lookup by _slotTimestamp, or SSZ merkleisation fail to bind the proof to the right slot, validator index, pubkey, withdrawal credentials or amount, so RocketMegapoolManager stakes, dissolves, exits or settles a validator on forged data and user ETH is stolen or frozen.",
    "Critical. Exited validator principal is settled or distributed wrongly, because RocketMegapoolManager.notifyFinalBalance accepts any proven withdrawal at or after withdrawable_epoch (a later small sweep, a withdrawal the attacker created by depositing to the exited pubkey, or one not bound to megapool credentials), or because returned principal sitting in the megapool is paid out by the permissionless distribute() as rewards before notifyExit, so rETH principal is split as node/voter/pDAO rewards, user shortfall escapes debt, or the node receives more than its bond.",
    "Critical. A permissionless node operator redirects assigned user ETH to a validator the megapool does not control, because newValidator/addValidator pubkey uniqueness (per-megapool, not global), the 1 ETH prestake in assignFunds, RocketMegapoolManager.stake checks (credentials, effective balance, activation epochs), dissolve/dissolveValidator timing, or a beacon-chain deposit the operator makes to their own pubkey let the 31 ETH top-up land on a validator with foreign withdrawal credentials or be recycled without charging the node.",
    "Critical. A node operator extracts more ETH than their bond from megapool accounting, because nodeBond, nodeQueuedBond, userCapital, userQueuedCapital, assignedValue, refundValue and debt drift across newValidator, dequeue, reduceBond, dissolveValidator, _calculateCapitalDispersal, _notifyFinalBalance, repayDebt and claim, so credit, refunds or rewards withdrawn to the withdrawal address come out of rETH holders' capital or leave debt that is never repaid.",
    "Critical. Deposit pool assignment loses or double-spends user ETH, because RocketDepositPool deposit, assignDeposits/_assignMegapools, express vs standard queue rotation in LinkedListStorage, requestedTotal and nodeBalance tracking in uint32 milli-ETH, exitQueue, applyCredit/withdrawCredit(For), recycleDissolvedDeposit, fundsReturned or the minipool-queue interplay let ETH be assigned twice, withdrawn from RocketVault without backing, mint rETH credit that was never deposited, or strand user ETH permanently.",
    "Critical. An rETH holder or depositor drains other holders, because RocketTokenRETH mint/burn, getEthValue/getRethValue, the deposit fee, deposit-delay transfer lock, getTotalCollateral, depositExcessCollateral and DepositPool.withdrawExcessBalance, combined with RocketNetworkBalances updates or ETH donated to megapools/minipools/distributors, allow a round-trip or sandwich that burns rETH for more ETH than was deposited, or lets burns pull ETH already reserved for assignment.",
    "Critical. A permissionless caller moves another node's funds, because RocketStorage withdrawal-address and RocketNodeManager RPL-withdrawal-address set/confirm/unset flows, onlyMegapoolOwner/isNodeCalling, RocketMegapoolProxy delegateUpgrade after expiry, RocketNodeStaking _callerAllowedFor/stakeRPLFor/unstake/withdraw, legacy vs megapool vs locked RPL checks, or RocketVault token accounting let the attacker redirect claims, withdraw staked RPL, bypass the unstaking period, or permanently lock a node's ETH or RPL.",
    "High. Unclaimed yield is stolen or permanently frozen, because RocketMerkleDistributorMainnet claim/claimAndStake bitmap, v0/v1 leaf encoding, outstanding-ETH fallback, RocketRewardsPool snapshots and voter share, RocketSmoothingPool, RocketNodeDistributorDelegate.distribute, legacy minipool distributeBalance/beginUserDistribute/refund, or megapool distribute timing with RocketNetworkRevenues time-weighted commission and RocketNetworkSnapshotsTime capital-ratio averaging let an attacker claim twice, claim for another node, or shift others' rewards to themselves.",
    "High. A permissionless node manipulates on-chain governance at a cost to others, because RocketNetworkVoting delegation snapshots, RocketDAOProtocolVerifier challenge/response voting-power trees, createChallenge/defeatProposal/submitRoot/claimBondChallenger/claimBondProposer, or RocketDAOProtocolProposal vote/overrideVote/finalise/execute let it inflate voting power, pass or defeat proposals against stake, double-claim or steal challenge/proposal RPL bonds, or lock another node's RPL indefinitely.",
    "Critical/High blind spot. An unprivileged user exploits an assumption Rocket Pool never wrote down: a value proven or checked in one contract and trusted as proven in another, ETH that arrives at a megapool/minipool/distributor/deposit pool without a code path accounting for it (donations, beacon sweeps, consolidation or EIP-7002 flows, selfdestruct), a check enforced on the megapool path but not its legacy-minipool, credit, express-ticket or migration twin, state carried across delegate upgrades, storage layouts or the v1.3 to v1.4 transition that was only safe in one version, or a setting boundary (zero, max, changed mid-flight) that flips an invariant - yielding theft of user principal, permanent freezing, or theft of unclaimed yield.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate exploit-focused audit and fuzzing questions for one rocketpool target.

    ```
    target_file format:
    "'File Name: contracts/contract/megapool/RocketMegapoolDelegate.sol -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate exploit-focused security audit questions for this exact rocketpool target:

    {target_file}

    Project focus:
    Rocket Pool is an Ethereum liquid staking protocol (v1.4 Saturn). Users deposit ETH for rETH. Permissionless node operators bond ETH in megapools (and legacy minipools) that borrow user ETH to run validators. Beacon-state proofs drive staking, exits and final balances. Focus on user principal, node bonds and debt, rETH backing, queue assignment, reward splits and RPL stake.

    Rules:
    * Treat `File Name:` as the exact contract.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Solidity symbols (contract, function, storage key) when possible.
    * Attacker is unprivileged only: any EOA or contract calling public functions, an rETH depositor or holder, a permissionless node operator acting on their own node/megapool/minipool and withdrawal addresses, an RPL staker, or a caller submitting real beacon-state proofs with a slot they choose. They may make real beacon-chain deposits to any pubkey and send ETH to any address.
    * Attacker is NOT the oDAO, a trusted node, the pDAO, the security council or the guardian, and never another node's withdrawal address. Assume oDAO balance/price/penalty/reward submissions are honest and beacon chain data is canonical. Never assume a malicious peer, beacon node, validator majority or consensus client.
    * Out of scope, never ask about: DoS, gas griefing, unbounded loops, storage/memory growth, centralization or privileged-role misuse, 51%/Sybil attacks, incorrect oracle data, best practices, and bugs whose only victim is the attacker.
    * Ignore test/helper/mock contracts (StakeHelper, MegapoolUpgradeHelper, StorageHelper, *Test, *Mock, RocketTokenDummyRPL), interfaces-only, scripts and config.
    * Every question must be a real on-chain scenario through a valid entry point: who calls which external function, with what inputs, in what state and order.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must target theft of user principal or node bonds, permanent freezing of ETH/RPL/rETH, unbacked rETH, or theft/freezing of unclaimed yield.
    * Every question must be testable by a Hardhat test in test/ (local mainnet fork where needed).
    * Avoid generic checklist questions and repeated root causes.

    Core invariants:
    * Principal is conserved: every wei of user capital assigned to a validator returns to rETH/deposit pool or becomes node debt; nothing is paid out as rewards.
    * rETH is backed: rETH supply times exchange rate never exceeds ETH the protocol can return, and mint/burn cannot be round-tripped for profit.
    * Proofs bind: a beacon proof binds to the right megapool, validator, pubkey, credentials, slot and the actual final withdrawal.
    * Bonds are honest: a node can withdraw only its bond, credit and earned rewards net of debt, never user capital.
    * Only owners move funds: only a node's own addresses can claim, withdraw or redirect its ETH, RPL or rewards, and each reward is claimed once.

    Each question must include:
    1. target contract/function;
    2. attacker role and action (calls, inputs, ETH/RPL sent, proofs or beacon deposits);
    3. preconditions (protocol and node state, settings);
    4. execution sequence;
    5. invariant tested;
    6. scoped impact;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: contract.function] Can an unprivileged ATTACKER_ROLE doing ATTACKER_ACTION under PRECONDITIONS trigger EXECUTION_SEQUENCE, violating INVARIANT, causing scoped impact: SCOPE_IMPACT? Proof idea: Hardhat test PARAMETERS and assert PRINCIPAL_CONSERVED, RETH_BACKED, PROOF_BINDS, BOND_HONEST, or OWNER_ONLY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a focused rocketpool exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: any caller of public functions, an rETH depositor/holder, a permissionless node operator on their own node, an RPL staker, or a submitter of real beacon proofs. They can make beacon-chain deposits and send ETH anywhere.
- Reject premises needing oDAO, trusted node, pDAO, security council, guardian, or another node's withdrawal address; dishonest oracle submissions; malicious peers, beacon nodes, or consensus majority.
- Reject DoS, gas griefing, unbounded loops or memory growth, centralization, 51%/Sybil, best practices, self-harm-only bugs, and test/helper/mock/interface/script findings.
- Focus on real impact: theft of user principal or node bonds, unbacked rETH, permanent or temporary freezing of funds, theft or freezing of unclaimed yield, or governance manipulation.

## Validate
- Trace the exact reachable path from the attacker's external call (inputs, ETH/RPL, proofs) into the affected function.
- Check whether modifiers (onlyMegapoolOwner, onlyRegisteredNode, onlyLatestContract), proof checks, debt/bond checks, delays, and settings bounds already stop it.
- Confirm it works with current mainnet settings, not only an extreme DAO setting.
- Accept only concrete loss, freeze, unbacked rETH, or yield theft with a quantified amount.
- Require exact file/function support and a reproducible Hardhat PoC (local fork only).

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[Code path, root cause, attacker inputs, exploit flow, and why existing checks fail]

### Impact Explanation
[Concrete impact, funds at risk, and matching category: Theft of Principal, Permanent Freezing, Temporary Freezing, Theft of Unclaimed Yield, or Governance Manipulation]

### Likelihood Explanation
[Preconditions, attacker cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Hardhat test plan with expected assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for rocketpool security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- Accepted severities (Immunefi, Rocket Pool):
  - Critical: direct theft of user principal (rETH backing, deposit pool, vault, node bonds), or permanent freezing of funds.
  - High: direct theft of unclaimed yield, governance manipulation with cost impact, or smaller-scale principal theft.
  - Medium: temporary freezing of funds, governance manipulation without cost impact, or small principal theft.
  - Low: only concrete, quantified unfair yield/commission manipulation. Reject pure griefing, informational, and best-practice reports.
- Reject anything requiring oDAO, trusted node, pDAO, security council, or guardian privileges, another node's withdrawal address, leaked keys, dishonest oracle submissions, malicious peers/beacon nodes/consensus majority, 51% or Sybil attacks.
- Reject DoS, gas griefing, unbounded loops or memory growth, centralization risk, self-harm-only bugs, and extreme DAO settings outside their enforced bounds.
- Reject test/helper/mock contracts (StakeHelper, MegapoolUpgradeHelper, StorageHelper, *Test, *Mock, RocketTokenDummyRPL), interfaces, scripts, and config.
- Reject if already fixed, acknowledged, publicly disclosed, or listed in Rocket Pool known issues or audits.
- A valid report must be triggerable by an unprivileged user (any caller, rETH holder, permissionless node operator on their own node, RPL staker, beacon-proof submitter) through a real external entry point.
- Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, contract, function, and line references.
2. Clear root cause and broken invariant (principal conserved, rETH backed, proof binds, bond honest, owner-only).
3. Reachable exploit path: preconditions -> attacker call/proof/deposit -> trigger -> loss or freeze.
4. Existing modifiers, proof checks, debt/bond checks, delays, and settings bounds reviewed and shown insufficient.
5. Concrete impact with funds at risk quantified and correct severity.
6. Reproducible Hardhat PoC on a local fork (no mainnet/testnet testing).
7. No obvious rejection reason from SECURITY.md, known issues, privilege assumptions, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Can an unprivileged user trigger this with current mainnet settings?
- Does the code actually behave as claimed, including onlyMegapoolOwner, debt, delay, and proof checks?
- Whose funds are lost or frozen, and how much?
- Is the loss permanent, temporary, or only yield?
- Would a Rocket Pool triager on Immunefi accept the PoC?
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
[Concrete impact, funds at risk, severity rationale, and Immunefi category]

## Likelihood Explanation
[Attacker capability, cost, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Hardhat test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for rocketpool.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof. The analog must stand on Rocket Pool's own code.
- Attacker is unprivileged only: any caller of public functions, an rETH depositor/holder, a permissionless node operator on their own node, an RPL staker, or a submitter of real beacon proofs with a slot they choose. They can make beacon-chain deposits and send ETH anywhere.
- Reject premises needing oDAO, trusted node, pDAO, security council, guardian, another node's withdrawal address, dishonest oracle data, or malicious peers/beacon nodes/consensus majority.
- Reject DoS, gas griefing, unbounded loops or memory growth, centralization, 51%/Sybil, self-harm-only, mocked-only paths, or no impact.
- Ignore test/helper/mock contracts, interfaces, scripts and config.

## Map the Bug Class
Pick the strongest reachable Rocket Pool surface for this class, then name the exact contract and function:
- Beacon proofs (forged state, wrong gindex, fork boundaries, stale/unbound slot, wrong withdrawal chosen): BeaconStateVerifier, SSZ, RocketMegapoolManager stake/dissolve/notifyExit/notifyNotExit/notifyFinalBalance.
- Principal vs rewards (exit ETH or donations paid as rewards, shortfall not charged): RocketMegapoolDelegate distribute/_notifyFinalBalance/getPendingRewards, RocketMinipoolDelegate distributeBalance/beginUserDistribute, RocketNodeDistributorDelegate.
- Bond, debt and credit accounting (withdraw more than bond, rounding, uint32 milli-ETH truncation): RocketMegapoolDelegate newValidator/dequeue/reduceBond/dissolveValidator/claim/_calculateCapitalDispersal, RocketNodeDeposit, RocketDepositPool applyCredit/withdrawCredit, RocketMinipoolBondReducer.
- Deposit queues and assignment (double assignment, skipped entries, stuck ETH): RocketDepositPool, LinkedListStorage, RocketMinipoolQueue, RocketVault.
- LST exchange rate (inflation, sandwich, rounding, fee bypass, withdrawal-queue races): RocketTokenRETH, RocketNetworkBalances, RocketDepositPool deposit/withdrawExcessBalance.
- Reward distribution (double claim, wrong leaf, timing or time-weighted commission manipulation): RocketMerkleDistributorMainnet, RocketRewardsPool, RocketSmoothingPool, RocketNetworkRevenues, RocketNetworkSnapshots(Time).
- Access and ownership (withdrawal-address hijack, proxy/delegate upgrade, storage layout collision, reentrancy via ETH send): RocketStorage, RocketNodeManager, RocketMegapoolProxy/StorageLayout, RocketMinipoolBase, RocketNodeStaking.
- Governance (vote power inflation, snapshot timing, bond theft in challenge games): RocketNetworkVoting, RocketDAOProtocolVerifier, RocketDAOProtocolProposal.

## Validate
- Trace the analog from a concrete unprivileged external call (inputs, ETH/RPL, proof, beacon deposit) into the named function.
- Show which invariant breaks: principal conserved, rETH backed, proof binds, bond honest, or owner-only.
- Confirm modifiers, proof checks, debt/bond checks, delays and settings bounds do not already stop it under current mainnet settings.
- Accept only theft of principal or bonds, unbacked rETH, permanent or temporary freezing, theft/freezing of unclaimed yield, or governance manipulation.
- Require a reproducible Hardhat PoC (local fork only).

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
