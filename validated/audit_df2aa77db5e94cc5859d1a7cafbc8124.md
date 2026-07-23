Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating EOA, allowing allowlist bypass via router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating EOA. If the router is allowlisted for a pool, every EOA — including those explicitly not allowlisted — can bypass the swap gate by calling through the router.

## Finding Description

`MetricOmmPool.swap` captures `msg.sender` and passes it verbatim as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates: [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap`. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no forwarding of the originating EOA: [4](#0-3) 

So when the path is `EOA → router.exactInputSingle → pool.swap`, `sender` seen by the extension is the router address. If `allowedSwapper[pool][router] = true`, the check passes for any EOA regardless of individual allowlist status. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` router entry points. [5](#0-4) 

## Impact Explanation

`SwapAllowlistExtension` is the sole per-pool swap gate. When bypassed, any EOA can execute swaps on a pool the admin intended to restrict. This breaks the core access-control functionality of the extension. For private or institutional pools, this allows unauthorized parties to execute swaps, causing direct loss of LP value through unwanted price impact or arbitrage. This constitutes broken core pool functionality under contest rules.

## Likelihood Explanation

The router is a public, permissionless contract. A pool admin who wants to allow router-mediated swaps for their allowlisted users has no other option than to allowlist the router address — the extension provides no mechanism to pass through the originating EOA. This is a natural and expected configuration. No attacker privilege is required; any EOA can call the public router.

## Recommendation

`SwapAllowlistExtension` should check the originating EOA rather than (or in addition to) the direct caller. Options include: (1) require the router to forward the originating EOA in `extensionData` and have the extension validate it against `tx.origin` or a signed permit; (2) explicitly document that allowlisting a router grants access to all router users and warn pool admins against it; (3) add a router-aware path in the extension that reads a caller address from `extensionData` and verifies it matches `tx.origin`.

## Proof of Concept

1. Deploy `SwapAllowlistExtension` and a pool configured to use it.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting only the router address.
3. `attackerEOA` (not individually allowlisted) calls `router.exactInputSingle(...)`.
4. Pool receives `pool.swap(...)` with `msg.sender = router` → extension sees `sender = router` → `allowedSwapper[pool][router] = true` → swap succeeds.
5. `attackerEOA` calls `pool.swap(...)` directly → extension sees `sender = attackerEOA` → `allowedSwapper[pool][attackerEOA] = false` → reverts with `NotAllowedToSwap`.

The per-user allowlist invariant is violated: an EOA blocked from direct swaps can freely swap through the router.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
