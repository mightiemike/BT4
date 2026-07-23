Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives `sender` equal to `msg.sender` of `pool.swap()`, which is the router contract when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to enable router-mediated swaps, every on-chain caller of the router passes the allowlist check regardless of individual allowlist status. LP principal in curated pools is directly at risk.

## Finding Description
**Exact call path:**

1. User (charlie, not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — the router is now `msg.sender` inside the pool. [1](#0-0) 
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the router address as `sender`. [2](#0-1) 
4. `ExtensionCalling._beforeSwap` forwards `sender` (router) unchanged to every configured extension. [3](#0-2) 
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, i.e., `allowedSwapper[pool][router]` — not `allowedSwapper[pool][charlie]`. [4](#0-3) 

**Root cause:** The allowlist mapping is keyed `allowedSwapper[pool][swapper]`. When the admin intends to gate individual users, they set `allowedSwapper[pool][alice] = true`. But the extension receives `sender = router`, so it evaluates a completely different key. If the admin also sets `allowedSwapper[pool][router] = true` to permit router-mediated swaps, that single entry grants every caller of the router a passing check.

**Existing guards are insufficient:** The only guard is the `allowedSwapper` / `allowAllSwappers` check in `beforeSwap`. There is no secondary check on the originating EOA. `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`. [5](#0-4) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted counterparties loses that guarantee the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and execute swaps against the pool's LP reserves at oracle-anchored prices. LP principal is directly at risk because the pool was deployed under the assumption that only vetted counterparties would trade against it. This constitutes a broken core pool functionality causing direct loss of LP assets, meeting the contest's Critical/High threshold.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery path for swaps. Pool admins who want to offer a usable UX will allowlist the router. Once `allowedSwapper[pool][router] = true` is set, the bypass is reachable by any unprivileged user with a single `exactInputSingle` call — no special setup, flash loan, or privileged access is required. The two broken outcomes (bypass if router is allowlisted; silently blocked allowlisted users if it is not) mean the admin cannot simultaneously enable router-mediated swaps and enforce per-user curation.

## Recommendation
Pass the original end-user address through the swap call so extensions can check it. The simplest fix is to have `MetricOmmSimpleRouter` encode `msg.sender` inside `callbackData` (or a dedicated `extensionData` field) and have `SwapAllowlistExtension.beforeSwap` decode and check that originator address instead of `sender`. Alternatively, add an `originator` parameter to the `beforeSwap` hook signature that the pool populates from router-supplied transient storage before dispatching to extensions.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][router] = true         // admin enables router path
  charlie = unprivileged address (not allowlisted)

Attack:
  charlie calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      tokenIn: token0,
      ...
  })

  → router calls pool.swap(recipient=charlie, ...)          // router is msg.sender
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; charlie receives token1 from LP reserves

Result:
  charlie, who is not individually allowlisted, successfully trades
  against a pool restricted to KYC'd users.
  LP principal is extracted at oracle-anchored prices with no recourse.
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
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
  }
```
