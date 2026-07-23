All four code references check out against the actual repository. The call chain is confirmed:

- `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — `msg.sender` is whoever called `pool.swap()` [1](#0-0) 
- `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap(...)` directly, making the router the `msg.sender` of that call [2](#0-1) 
- `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `sender` = router [3](#0-2) 
- `DepositAllowlistExtension.beforeAddLiquidity()` ignores `sender` (first arg `_`) and checks `owner` instead [4](#0-3) 

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` Checks Router Address Instead of Actual Swapper, Enabling Per-User Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router address, not the initiating user. If the router is allowlisted, every user of the router — including those not individually allowlisted — can trade on a curated pool, silently collapsing the per-user gate. Conversely, if only individual users are allowlisted (not the router), those users cannot swap through the standard periphery path at all.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, passing its own `msg.sender` as the `sender` argument forwarded to all extensions. When `MetricOmmSimpleRouter.exactInputSingle()` is the caller, it invokes `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly, so `msg.sender` inside `pool.swap()` is the router contract. `ExtensionCalling._beforeSwap()` then passes this router address verbatim as `sender` to `SwapAllowlistExtension.beforeSwap()`, which evaluates `allowedSwapper[pool][router]`. The actual initiating user is never examined. By contrast, `DepositAllowlistExtension.beforeAddLiquidity()` discards `sender` entirely (declared as `_`) and checks `owner` — the economic beneficiary — demonstrating that the correct pattern is known and applied elsewhere in the same codebase. The swap extension applies the check to the wrong entity.

**Bypass path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`; `allowAllSwappers = false`.
2. Admin calls `setAllowedToSwap(pool, router, true)` — a natural action to enable the standard periphery.
3. Unprivileged user Charlie (not individually allowlisted) calls `router.exactInputSingle({pool, recipient: charlie, ...})`.
4. Router calls `pool.swap(charlie, ...)` → `msg.sender` of `pool.swap()` = router.
5. Pool calls `extension.beforeSwap(sender=router, ...)` → `allowedSwapper[pool][router] = true` → passes.
6. Charlie's swap executes on the restricted pool. Any router caller bypasses the per-user gate.

**Broken path for legitimate users:** If admin allowlists individual users but not the router, those users' calls through the router revert because the extension sees the router address and finds it not allowlisted.

## Impact Explanation
The curated-pool invariant — "only approved counterparties may trade" — is silently violated. LP assets are exposed to unauthorized swap flow from any address that can call the public router. This constitutes broken core pool functionality: the allowlist extension, the primary mechanism for restricting swap access on curated pools, fails to enforce its intended gate when the canonical periphery path is used. The secondary impact (legitimate allowlisted users unable to swap through the router) renders the standard periphery path unusable for the very users the admin intended to serve.

## Likelihood Explanation
The router is the documented, deployed user-facing swap entry point. A pool admin configuring a curated pool will naturally allowlist the router as a trusted intermediary — the bypass requires no special privilege or knowledge. Any unprivileged address can call the public `exactInputSingle` function. The condition (router allowlisted) is the expected operational state, not an edge case, making exploitation straightforward and repeatable.

## Recommendation
The extension must gate the actual initiating user, not the intermediary. The most robust fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and check it — establishing a convention between router and extension. Alternatively, the extension could check `recipient` as a closer proxy for the economic actor, though this is imperfect when recipient differs from initiator. The `DepositAllowlistExtension` pattern — checking `owner` (the economic actor passed explicitly by the pool) rather than `sender` — should be the model. At minimum, documentation must explicitly warn that allowlisting the router collapses the per-user gate.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension; allowAllSwappers[pool] = false.
2. Pool admin: setAllowedToSwap(pool, router, true).
3. Charlie (EOA, not in allowedSwapper): calls
     router.exactInputSingle({pool: pool, recipient: charlie, zeroForOne: true, amountIn: X, ...})
4. Router executes: pool.swap(charlie, true, X, limit, "", extensionData)
     → msg.sender of pool.swap() = router
5. Pool: _beforeSwap(msg.sender=router, charlie, ...)
     → extension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] = true → no revert
6. Swap settles. Charlie traded on a pool that should have rejected him.
   Repeat with any address — all bypass the per-user allowlist via the router.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
```
