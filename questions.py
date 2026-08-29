import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'Zest-Protocol/zest-v2-contracts'
# todo: the name of the repository
REPO_NAME = 'zest-v2-contracts'

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
    # LENS: TIME, STALENESS AND INTERNAL ORDERING.
    #
    # Zest never validates and acts in one step. Every entry point reads a position,
    # accrues some subset of it, primes a cache keyed on `stacks-block-time`, resolves
    # prices, checks health, and only then moves money - and several entry points call
    # out to a vault in between, changing the very state the check was based on. Meanwhile
    # `accrue` is idempotent per timestamp, `last-update` advances only when an index
    # actually changed, the `accrue` pause is a silent pass-through rather than a revert,
    # and half a dozen folds carry a `valid` or `success` flag that absorbs a failure
    # instead of aborting. This variant hunts CHECK-THEN-ACT gaps, stale reads, and
    # failures that do not fail - within a single user's transaction.
    #
    # Cross-user ordering belongs to another variant. So do value identities and pricing.
    # Excluded by program rules: all flashloan logic, anything needing DAO compromise or a
    # bad registry update, and liquidation of disabled collateral.
    # =================================================================================

    # -- Where every check-then-act gap lives -------------------------------------------
    # accrue-and-cache and the `index-cache` keyed on `stacks-block-time`; the accrual
    # ordering inside borrow, repay, collateral-add and collateral-remove; the three-step
    # composite entry points supply-collateral-add, collateral-remove-redeem and
    # liquidate-redeem; liquidate-multi sharing one snapshot across N seizures; and the
    # folds that swallow failure - write-feed, iter-price-multi, socialize-debt-asset,
    # accrue-debt-asset, accrue-collateral-asset, remove-if-match, iter-find-asset.
    "mainnet/contracts/market/v0-4-market.clar",

    # -- Where the clocks are written ----------------------------------------------------
    # `refresh` stamping `last-update` on every write, `debt-add-scaled` stamping
    # `last-borrow-block` from `stacks-block-height` while every index uses
    # `stacks-block-time`, and the let-bound mutations that run before the assertions.
    "mainnet/contracts/market/v0-market-vault.clar",

    # -- Where time becomes money ---------------------------------------------------------
    # accrue, next-index, next-liquidity-index, calc-multiplier-delta, the `last-update`
    # var advanced only on change, the pause pass-through, and the preview functions that
    # re-derive a future index inside a call that already applied it.
    # v0-vault-stx is the native-STX path; v0-vault-sbtc the 8-decimal comparison;
    # v0-vault-ststxbtc the newest underlying and the one least exercised by tests.
    "mainnet/contracts/vault/v0-vault-stx.clar",
    "mainnet/contracts/vault/v0-vault-sbtc.clar",
    "mainnet/contracts/vault/v0-vault-ststxbtc.clar",
]


target_scopes = [
    "Critical. THE CACHE IS NEVER INVALIDATED WHEN THE VAULT MOVES. `accrue-and-cache` keys `index-cache` on `{{ timestamp: stacks-block-time, aid }}` and returns the stored record on every later read in that block, but nothing clears an entry when the underlying vault's `index`, `lindex` or `assets` change afterwards - a deposit, a redeem, a `system-borrow` or a `system-repay` in the same transaction. Show a single transaction that primes the cache, mutates the vault, and then makes a decision from the stale cached record. Impact: protocol insolvency, or direct theft of user funds.",

    "Critical. `last-update` ONLY MOVES WHEN AN INDEX MOVED. `accrue` sets `last-update` to `stacks-block-time` only inside `(if (or (not (is-eq idx next)) (not (is-eq lidx nliq))) ...)`. Any interval in which the computed multiplier rounds to `INDEX-PRECISION` - a zero rate, a tiny `time-delta`, a vault with no debt - leaves `last-update` stale, so the NEXT accrual computes `time-delta` over an interval that already elapsed. Show interest charged twice for the same seconds, or a `lindex` that grows on time nobody borrowed for. Impact: protocol insolvency, or theft of unclaimed yield.",

    "Critical. THE ACCRUE PAUSE IS A SILENT PASS-THROUGH. When the `accrue` pause state is set, `accrue` returns `{{ index: idx, lindex: lidx }}` without reverting, and `next-index` and `next-liquidity-index` return the stale values, while `deposit`, `redeem`, `system-borrow` and `system-repay` remain independently controlled. Establish exactly which operations still move money against a frozen index, and what happens on unpause when one `calc-multiplier-delta` covers the entire paused interval at the rate prevailing at that instant. Impact: theft of unclaimed yield, and insolvency on resumption.",

    "Critical. `borrow` PRICES ASSETS IT HAS NOT ACCRUED. Its `let` runs `write-feeds`, then `get-position`, then `accrue-user-debts`, then `accrue-user-collateral`, then `accrue-and-cache` for the borrowed asset, and only then `get-assets` and the notional evaluation. Establish which assets are guaranteed to be in the cache when `resolve-ztoken` reads `get-cached-indexes`, and find a position composition where a zToken collateral is priced from an index primed for a different asset, from a cold cache, or from an entry written before the borrowed asset accrued. Impact: protocol insolvency.",

    "Critical. `collateral-add` PRIMES THE CACHE ONLY IF YOU ALREADY HAVE DEBT. The `cache-primed` binding that calls `accrue-and-cache` for a new zToken collateral's underlying vault sits inside the `(> current-debt-usd u0)` branch; the no-debt path and the not-new-collateral path skip it entirely, as does the new-user branch. Show a transaction that adds zToken collateral through a path that leaves the cache cold, then relies on `resolve-ztoken` later in the same transaction or the same block. Impact: protocol insolvency, or a health path that aborts and freezes the position.",

    "Critical. `collateral-remove-redeem` CHECKS HEALTH, THEN CHANGES THE PRICE. Step one calls `collateral-remove`, which resolves prices and asserts `is-healthy` on the post-removal position; step two calls `vault-redeem`, which burns shares and moves the vault's `assets` and therefore the `lindex` that values every REMAINING zToken collateral in that same position. Show a position that is healthy at the moment of the check and under-collateralised the instant the redeem completes, with the funds already gone. Impact: protocol insolvency.",

    "Critical. `supply-collateral-add` MAKES THREE STATE CHANGES AND CHECKS ONCE. It transfers the underlying to the market, deposits it under an `as-contract?` scope, and then calls `collateral-add` with a different trait principal - so the deposit that moved the vault's share price happens BEFORE the only health and egroup validation in the call. Establish what `shares-minted` is worth at each of the three points, and whether the capacity comparison `(>= future-capacity current-capacity)` is evaluated against pre-deposit or post-deposit state. Impact: protocol insolvency.",

    "Critical. `liquidate` READS THE POSITION BEFORE THE WRITES IT AUTHORIZES. `position`, `pos-full`, `mask`, `group` and every price are bound at the top of the `let`, then the debt repayment, the collateral seizure and any socialization execute in sequence, each mutating vault state the later steps still evaluate against the original bindings. Show a multi-asset seizure in which the amount seized for the second asset is computed from a `lindex` or `index` the first asset's repayment already changed. Impact: direct theft of user funds.",

    "Critical. `socialize-debt-asset` REWRITES THE CACHE IT IS ITERATING UNDER. Inside the fold it calls `vault-socialize-debt`, then writes `(vault-accrue asset-id)` directly into `index-cache` for the current timestamp, replacing the record earlier steps of the same transaction already consumed. Show a transaction in which values computed before that refresh are combined with values computed after it - a seizure sized on the old `lindex` settled against the new one, or a debt total that no longer matches the rows it was derived from. Impact: protocol insolvency.",

    "Critical. FAILURES THAT DO NOT FAIL. `write-feed` folds a `(response bool uint)`; `iter-price-multi` carries a `valid` flag and returns the accumulator unchanged once it is false; `socialize-debt-asset` carries a `success` flag and short-circuits; `accrue-debt-asset` and `accrue-collateral-asset` call `unwrap-panic` inside a fold whose accumulator ignores the result; `iter-find-asset`, `iter-find-collateral`, `iter-find-debt` and `remove-if-match` return silently when nothing matches. For each, determine whether a failure aborts the transaction or is absorbed, and find the one where an absorbed failure lets the call proceed on incomplete state. Impact: protocol insolvency, or direct theft.",

    "Critical. A ZERO RESULT AND A MISSING ROW ARE INDISTINGUISHABLE. `find-collateral-amount` and `find-debt-scaled` both return `u0` when the asset is absent from the list, and `find-asset` returns `none` which several callers resolve with `unwrap-panic` or a default. Enumerate every decision made on those return values - `removing-all` in `collateral-remove`, the `(> coll-amount u0)` and `(> debt-scaled u0)` guards in the notional fold, `curr-scaled` in the liquidation path - and show a real holding treated as absent, or an absence treated as a zero holding, at a moment that moves money. Impact: protocol insolvency, or direct theft.",

    "Critical. THE LEDGER MUTATES BEFORE THE GUARDS RUN. In market-vault `collateral-add`, `collateral-remove`, `debt-add-scaled` and `debt-remove-scaled`, the map write and `mask-update` are `let` bindings evaluated before `check-impl-auth`, the pause state and the amount assertion; `resolve-or-create` consumes `increment` in the same position. Determine precisely what persists when a later assertion fails, across every call-boundary and `as-contract?` scope in scope, and whether any identifier, mask, or partially updated row survives a rejected call. Impact: permanent freezing of funds.",

    "Critical. `deposit` APPLIES THE FUTURE INDEX TWICE. It calls `(try! (accrue))`, which writes `index` and `lindex`, and then computes `inkind` from `convert-to-shares-preview`, which reaches `total-assets-preview` and hence `debt-preview` and `next-index` - re-deriving a forward index from the values `accrue` just wrote. Establish whether `time-delta` is genuinely zero at that point on every path, including when the pause state made `accrue` a no-op or when `last-update` was left stale, and show shares minted against interest counted twice. Impact: theft of supplier principal.",

    "Critical. `redeem` BINDS ITS BALANCES AROUND THE ACCRUAL. Its `let` binds `current-assets`, `balance`, `available-assets` and `inkind` in sequence with `(try! (accrue))` among them, then asserts against those bindings and calls `ft-burn?` and `send-underlying`. Establish the exact evaluation order, which bindings therefore reflect pre-accrual state, and whether `(>= current-assets inkind)` and `(>= available-assets inkind)` can both pass against values that no longer describe the vault when the transfer executes. Impact: protocol insolvency, or permanent freezing for the remaining suppliers.",

    "High. `repay` TAKES ITS INDEX FROM A CACHE IT MAY NOT HAVE PRIMED. It calls `accrue-user-debts` over the position's debt list, then reads `(get index (unwrap-panic (get-cached-indexes asset-id)))`. Establish what happens when the repaid asset is not in that list - a debt row filtered out by the enabled mask, a repayment on behalf of an account with no such row, an asset accrued under a different id - and whether the `unwrap-panic` aborts or an unrelated index is used to size the repayment. Impact: permanent freezing of an unrepayable debt, or theft of unclaimed yield.",

    "High. TWO CLOCKS, TWO UNITS. Every index uses `stacks-block-time` while `last-borrow-block` is stamped from `stacks-block-height`, and `is-liquidation-paused` compares grace entries against time. Establish where the two are compared, defaulted or mixed, what `(- stacks-block-time (var-get last-update))` does if `last-update` is ever ahead of the current time, and whether the same-block borrow guard can be satisfied or defeated by the mismatch. Impact: protocol insolvency, or temporary freezing of liquidations.",

    "High. `liquidate-multi` RUNS N LIQUIDATIONS ON ONE SNAPSHOT. `call-liquidate` passes `none` for `price-feeds`, so the whole batch inherits whatever oracle `last-update` and `index-cache` state the transaction began with, and each iteration mutates the vault state the next is evaluated against. Show a batch in which a later entry is priced or sized against state that three earlier seizures have already invalidated, and identify whether the failure of one entry aborts the batch or is absorbed. Impact: direct theft of user funds.",

    "High. `refresh` RESETS A CLOCK OTHER LOGIC READS. Every market-vault write merges `(refresh mask)`, setting `last-update` to `stacks-block-time` while carrying `last-borrow-block` forward, and `debt-add-scaled` alone writes the borrow stamp. Establish everything that reads the position's `last-update`, and show an operation - including a dust `repay` or a collateral top-up - that resets it in a way that changes whether a later check passes. Impact: protocol insolvency, or temporary freezing of a liquidation.",

    "High. THE MULTIPLIER ROUNDS IN OPPOSITE DIRECTIONS FOR THE SAME INTERVAL. `next-index` calls `calc-multiplier-delta` with round-up and `next-liquidity-index` calls it with round-down over the same `time-delta` and the same rate, and `calc-liquidity-rate` additionally scales by utilization and `fee-reserve`. Show an interval and rate at which the debt index advances while the liquidity index does not, repeated across many small intervals, so borrowers are charged interest that no supplier and no treasury ever receives. Impact: theft of unclaimed yield.",

    "High. A ZERO `time-delta` IS SPECIAL-CASED, A ZERO RATE IS NOT. Both index functions substitute `INDEX-PRECISION` when `time-delta` is zero but otherwise call `calc-multiplier-delta` unconditionally, and `interest-rate` interpolates from packed curve points that can produce zero. Establish what the multiplier and the resulting index become when the rate is zero over a long interval, when `time-delta` is enormous after a dormant period, and whether either case aborts or silently freezes accrual for that vault. Impact: temporary or permanent freezing of funds, or theft of unclaimed yield.",

    "High. PARTIAL FAILURE STRANDS VALUE IN THE MARKET. `supply-collateral-add` and `collateral-remove-redeem` both leave the market contract transiently holding user tokens or shares between steps, and `liquidate-redeem` does the same during a seizure. Enumerate every way a later step can fail - a slippage bound, a cap, a paused state, an aborting price path - and determine for each whether the whole transaction unwinds or whether tokens are left on the market contract with no accounting row and no way for the owner to reclaim them. Impact: permanent freezing of funds.",

    "Critical. CHECK-THEN-ACT ACROSS A CONTRACT BOUNDARY - the seam nobody modelled. Enumerate every point in the protocol where a Zest contract validates a condition, then calls out to another contract, then acts on the value it read BEFORE the call. Include market to vault, market to market-vault, market to registry, vault to `.wstx` and to the underlying token, and every attacker-supplied `<ft-trait>` invocation. For each, name the value read, the call made, and whether that call can change the value. Then find the one where it can, and prove with a single simnet transaction that the value used to move money differs from the value the safety check approved. Impact: name it as direct theft, permanent freezing, or protocol insolvency.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate ordering- and staleness-focused audit questions for one Zest v2 target.

    ```
    target_file format:
    "'File Name: mainnet/contracts/vault/v0-vault-stx.clar -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate ordering, staleness and atomicity security audit questions for this exact Zest Protocol v2 target:

    {target_file}

    Project focus:
    Zest v2 never validates and acts in one step. A typical entry point reads a position, accrues
    part of it, primes `index-cache` on `{{ timestamp: stacks-block-time, aid }}`, resolves prices
    from that cache, checks health, and only then moves money - and `supply-collateral-add`,
    `collateral-remove-redeem` and `liquidate-redeem` call out to a vault in between, changing the
    state the check was based on. `accrue` is idempotent per timestamp and advances `last-update`
    only when an index actually changed; the `accrue` pause state is a silent pass-through, not a
    revert; `next-index` rounds up while `next-liquidity-index` rounds down over the same interval;
    the preview functions re-derive a forward index inside calls that already accrued. Debt indexes
    key on `stacks-block-time` while `last-borrow-block` uses `stacks-block-height`. Several folds
    - `write-feed`, `iter-price-multi`, `socialize-debt-asset`, `accrue-debt-asset`,
    `remove-if-match`, `iter-find-asset` - carry a flag or return the accumulator unchanged rather
    than aborting, and market-vault mutates its maps in `let` bindings evaluated before its guards.

    Every question must be about a SINGLE user's transaction or a single block's internal ordering.
    Two-user interference belongs to a different batch and must not be generated here.

    Rules:
    * Treat `File Name:` as the exact contract.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Clarity symbols (define-public/private/read-only names, map, data-var, constant).
    * Every question must name the VALUE READ, the EVENT that changes it, and the LATER USE that
      still relies on the earlier read - or the FAILURE that is absorbed instead of aborting.
    * Attacker is unprivileged only: an ordinary Stacks principal that funds a wallet, calls any
      public function, deploys its own Clarity contract, passes it as `<ft-trait>`, supplies its
      own `price-feeds`, and chooses amounts, recipients and the order of its own calls.
    * Attacker is NOT a DAO signer, executor, market impl, authorized contract, miner, oracle
      publisher or node operator. Ignore malicious-miner, chain-reorg, MEV-only and
      social-engineering assumptions.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - ANY logic related to flashloans is OUT OF SCOPE. A flashloan may be used as a source of
        capital for a different attack, but never target `flashloan` itself, its fee, its
        `flashloan-permissions` / `default-flashloan-permissions` whitelist, or `in-flashloan`.
      - Liquidation of disabled collateral, and any other deliberate protocol safety design
        decision, is OUT OF SCOPE.
      - Anything requiring DAO compromise, or an accidental or incorrect registry update by the
        DAO, is OUT OF SCOPE. Full DAO control of the asset and egroup registries is intended
        design, and every egroup invariant needing global market and position knowledge is
        verified off-chain by the DAO before approval. Assume both registries are correctly
        configured, and target only the read and resolution paths an ordinary user call executes.
      - Also excluded everywhere: leaked keys or credentials, privileged addresses, external
        stablecoin depegs the attacker did not cause through a bug in this code, 51% and basic
        economic or governance attacks, Sybil attacks, centralization risk, lack of liquidity,
        incorrect data supplied by third-party oracles, best-practice notes, feature requests,
        and test or configuration files.
      - Oracle manipulation caused by a bug in THIS code remains fully in scope.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: direct theft of user funds at rest or in motion, other than unclaimed yield;
      permanent freezing of funds; protocol insolvency.
      High: theft of unclaimed yield or royalties; permanent freezing of unclaimed yield or
      royalties; temporary freezing of funds.
    * Every question must be a concrete real-world scenario on mainnet, with the interleaving
      written out step by step. No speculative unbounded-list, memory or resource-hygiene questions.
    * Clarity `+` `-` `*` abort on overflow and underflow. Here an abort matters when a stale or
      uninitialised value makes a required user or liquidator action impossible - say which action
      and whether the block is permanent or temporary.
    * Generate 30 to 40 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable by a Clarinet / vitest simnet test in `local-testing/tests`
      on a local fork, using explicit block advancement or an intra-transaction sequence. Never
      propose testing on mainnet or a public testnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions of the form: state the value at step 1, the mutation at step 2, and the use
      at step 3, then ask whether step 3 is still entitled to the step 1 value.

    Known dead ends - do NOT generate questions about these:
    * Two different users interfering with each other - that is another batch.
    * Governance pausing, unpausing, or setting a parameter as designed.
    * An external oracle or token misbehaving on its own.
    * A user harming only its own position with no protocol invariant broken.
    * Anything only reproducible against mock tokens or the mock oracle.

    Core temporal invariants (each question must break one):
    * CACHE FIDELITY: a value read from `index-cache` describes the vault as it is at the moment of
      use, not as it was when the entry was written.
    * ACCRUAL EXACTNESS: every second of elapsed time is charged exactly once, to exactly one
      index, in exactly one direction.
    * CHECK-THEN-ACT INTEGRITY: the state a safety check approved is the state the money movement
      executes against.
    * FAILURE PROPAGATION: a failed sub-step aborts the transaction or is explicitly compensated;
      no decision proceeds on partial state.
    * ATOMIC SETTLEMENT: when a multi-step entry point aborts, no value is left stranded and no
      identifier, mask or partial row survives.

    Each question must include:
    1. target function/method;
    2. the value read and where it is bound;
    3. the intervening event that changes it (a vault call, a redeem, a socialization, a pause, a
       block boundary);
    4. the later use that still relies on the earlier read;
    5. the temporal invariant broken;
    6. the in-scope impact class;
    7. proof idea with the interleaving spelled out.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Function: symbol_or_method] Is VALUE_READ bound at STEP_1 still valid at STEP_3 after EVENT at STEP_2, under PRECONDITIONS, or does the gap violate INVARIANT and cause IMPACT_CLASS: SCOPE_IMPACT? Proof idea: Clarinet simnet test PARAMETERS with the interleaving INTERLEAVING and assert CACHE_FIDELITY, ACCRUAL_EXACTNESS, CHECK_THEN_ACT_INTEGRITY, FAILURE_PROPAGATION, or ATOMIC_SETTLEMENT.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate an ordering-focused Zest v2 exploit-validation prompt.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- The claim must concern a single user's transaction or one block's internal ordering. Two-user interference is out of band for this review.
- Attacker is unprivileged only: an ordinary Stacks principal that funds a wallet, calls any public function, deploys its own Clarity contract and passes it as `<ft-trait>`, supplies its own `price-feeds`, and chooses amounts, recipients and its own call order. No DAO signer, executor, market impl, authorized contract, miner, oracle publisher or node operator access.
- Reject malicious-miner, chain-reorg, MEV-only and social-engineering paths.
- OUT OF SCOPE, reject on sight: any flashloan logic (`flashloan`, its fee, its permission whitelist, `in-flashloan`) - though a flashloan used purely as capital for a different attack is fine; liquidation of disabled collateral and other deliberate safety design decisions; anything requiring DAO compromise or an accidental or incorrect DAO registry update, since full DAO control of the asset and egroup registries is intended design and egroup invariants needing global position knowledge are verified off-chain before approval.
- Also reject: leaked keys, privileged addresses, external stablecoin depegs the attacker did not cause through a bug here, 51% / basic economic / governance attacks, Sybil, centralization risk, lack of liquidity, incorrect data supplied by third-party oracles, best-practice notes, feature requests, and test or configuration files. Oracle manipulation caused by a bug in THIS code stays in scope.
- The impact must be one of: Critical - direct theft of user funds at rest or in motion other than unclaimed yield, permanent freezing of funds, or protocol insolvency; High - theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties, or temporary freezing of funds.
- Reject Pyth and Wormhole internals, third-party token behaviour, `local-testing/**`, tests, mocks, deployment plans, docs, read-only aggregators, and dependency-only findings.

## Validate
- Write the sequence out as numbered steps, marking every `let` binding, every cross-contract call, and every block boundary.
- For the value in question, record where it is bound, every write to its source between binding and use, and the exact expression that consumes it.
- Determine whether Clarity's evaluation order actually produces the interleaving claimed - `let` bindings evaluate in order, and a `try!` inside a binding runs at binding time.
- Establish whether the stale or absorbed value changes the outcome, or whether a later assertion, slippage bound, cap or health check recovers it.
- For absorbed-failure claims, show exactly which failure is swallowed and what decision then proceeds on partial state.
- For atomicity claims, confirm whether the transaction actually unwinds; if it does, there is no finding unless value or an identifier is stranded outside the rollback.
- Require exact file/function support and a reproducible Clarinet / vitest simnet PoC on a local fork that reproduces the interleaving.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The numbered sequence, the value bound and the event that invalidates it, root cause, attacker inputs, and why later checks do not recover]

### Impact Explanation
[What moves or freezes as a result, magnitude, and the exact in-scope severity category]

### Likelihood Explanation
[Preconditions, whether the interleaving is attacker-chosen or incidental, capital cost, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[Clarinet simnet test plan on a local fork with the interleaving and block advancement spelled out]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for Zest v2 ordering claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- An ordering claim is only valid if the report gives the numbered sequence, the binding point of the stale value, and the write that invalidates it. Reject prose-only claims.
- Verify the claimed interleaving is actually possible under Clarity evaluation order; reject any claim that depends on `let` bindings evaluating out of order or on a `try!` deferring.
- Reject anything requiring a DAO signer, executor, market impl, authorized contract, miner, oracle publisher, node operator, or leaked keys.
- OUT OF SCOPE, reject on sight: any flashloan logic (`flashloan`, its fee, its permission whitelist, `in-flashloan`) - though a flashloan used purely as capital for a different attack is fine; liquidation of disabled collateral and other deliberate safety design decisions; anything requiring DAO compromise or an accidental or incorrect DAO registry update, since full DAO control of the asset and egroup registries is intended design and egroup invariants needing global position knowledge are verified off-chain before approval.
- Also reject: leaked keys, privileged addresses, external stablecoin depegs the attacker did not cause through a bug here, 51% / basic economic / governance attacks, Sybil, centralization risk, lack of liquidity, incorrect data supplied by third-party oracles, best-practice notes, feature requests, and test or configuration files. Oracle manipulation caused by a bug in THIS code stays in scope.
- The impact must be one of: Critical - direct theft of user funds at rest or in motion other than unclaimed yield, permanent freezing of funds, or protocol insolvency; High - theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties, or temporary freezing of funds.
- Reject Pyth and Wormhole internals, third-party contracts, `local-testing/**`, tests, mocks, deployment plans, `.toml`, docs, read-only aggregator and dependency-only findings.
- Reject if the bug was already fixed, acknowledged, or covered by the published Clarity Alliance, Greybeard or Asymmetric audits.
- Reject if the transaction simply unwinds, unless value or an identifier survives the rollback.
- Reject any PoC requiring testing on mainnet or a public testnet; only local forks are permitted.
- A PoC is mandatory for every severity. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line/code references.
2. The sequence written as numbered steps with bindings, cross-contract calls and block boundaries marked.
3. The stale or absorbed value identified, with its binding point and the write that invalidates it.
4. The interleaving confirmed possible under Clarity evaluation semantics.
5. Later assertions, slippage bounds, caps, health checks and pause states reviewed and shown not to recover.
6. Concrete in-scope impact class named, with the value moved or frozen quantified.
7. Reproducible proof: Clarinet / vitest simnet test on a local fork reproducing the interleaving.

## Silent Triage Questions
Before output, internally answer:
- What exactly is read, what changes it, and what still uses the old value?
- Is that interleaving reachable given how Clarity evaluates `let` and `try!`?
- Does the transaction abort anyway, and if so does anything survive?
- Is the failure genuinely absorbed, or does it propagate?
- Which in-scope impact class does it land on?
- What exact test, with which block advancement, would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the ordering gap and impact]

## Finding Description
[Exact code path, numbered sequence, root cause, and why later checks fail]

## Impact Explanation
[Value moved or frozen, duration, and the exact in-scope category]

## Likelihood Explanation
[Attacker capability, preconditions, control over the interleaving, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or Clarinet simnet test plan on a local fork]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project ordering analog scan prompt for Zest v2.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope production repo context only (`mainnet/contracts/**`, excluding the dao directory). Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only single-transaction or single-block analogs: a cached value not invalidated when its source moves, a clock advanced only on change, a pause that passes through instead of reverting, a health check followed by a call that changes what it checked, a fold that absorbs failure, a mutation evaluated before its guard, or a multi-step entry point that strands value on abort.
- Reject analogs whose mechanism is two different users interfering with each other.
- OUT OF SCOPE, reject on sight: any flashloan logic (`flashloan`, its fee, its permission whitelist, `in-flashloan`) - though a flashloan used purely as capital for a different attack is fine; liquidation of disabled collateral and other deliberate safety design decisions; anything requiring DAO compromise or an accidental or incorrect DAO registry update, since full DAO control of the asset and egroup registries is intended design and egroup invariants needing global position knowledge are verified off-chain before approval.
- Also reject: leaked keys, privileged addresses, external stablecoin depegs the attacker did not cause through a bug here, 51% / basic economic / governance attacks, Sybil, centralization risk, lack of liquidity, incorrect data supplied by third-party oracles, best-practice notes, feature requests, and test or configuration files. Oracle manipulation caused by a bug in THIS code stays in scope.
- The impact must be one of: Critical - direct theft of user funds at rest or in motion other than unclaimed yield, permanent freezing of funds, or protocol insolvency; High - theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties, or temporary freezing of funds.
- Reject malicious-miner, chain-reorg, MEV-only, oracle-publisher, third-party token, `local-testing/**`, mock, deployment-plan, dependency-only and no-impact analogs.

## Validate
- Map the bug class to the strongest reachable Zest path and write the sequence as numbered steps.
- Name the value bound, the event that invalidates it, and the later use.
- Confirm the interleaving is possible under Clarity evaluation order.
- Prove root cause with exact file/function support.
- Name the in-scope impact class it lands on.

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
