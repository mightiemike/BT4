Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of the originating EOA, enabling allowlist bypass for any user routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the value passed from `MetricOmmPool.swap()` as `msg.sender` of the pool call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user's EOA. If the pool admin allowlists the router to enable the standard periphery path, any unpermissioned user can bypass the curated allowlist entirely by routing through the router.

## Finding Description

**Call chain — `sender` is bound to the immediate caller of `pool.swap()`:**

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Router path — pool always sees `msg.sender = router`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly; the pool sees `msg.sender = router`, not the user's EOA: [4](#0-3) 

For multi-hop `exactInput`, intermediate hops use `address(this)` (the router) as payer, and the pool still sees `msg.sender = router`: [5](#0-4) 

**Two irreconcilable broken outcomes:**

| Pool admin intent | Configuration | Actual result |
|---|---|---|
| Allow specific EOAs, block others | Allowlist EOA addresses | Allowlisted EOAs cannot use the router; must call `pool.swap()` directly |
| Allow specific EOAs to use the router | Allowlist the router address | **Any** user can swap through the router — allowlist fully bypassed |

There is no configuration that simultaneously restricts swaps to specific users and lets those users use `MetricOmmSimpleRouter`. The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][userEOA]`.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to create a permissioned trading venue (e.g., KYC-gated, institutional-only) cannot enforce that policy when users route through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to enable the standard periphery path, the allowlist is rendered inoperative for all router-mediated swaps, allowing any unpermissioned user to trade on the pool. This is a direct admin-boundary break: an unprivileged path bypasses a configured access control guard, enabling unauthorized fund flows through the pool.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for EOAs. Any pool that configures `SwapAllowlistExtension` and expects users to use the router will encounter this issue. The misconfiguration arises from the natural expectation that allowlisting a user means that user can use all supported periphery paths. No special attacker capability is required — any user can call `exactInputSingle` or `exactInput` on the router.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should gate on the original transaction initiator. The cleanest fix is to have the router encode `msg.sender` (the original caller) into `extensionData`, and have the extension decode and check that address when `sender` is a known router. Alternatively, the extension can fall back to `tx.origin` when `sender` is a known router contract, though `tx.origin` has its own caveats. A dedicated "initiator" field passed through the pool's extension data pipeline would be the most robust solution.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin does **not** allowlist `userB` (an unpermissioned address).
4. `userB` calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. The router calls `pool.swap(...)` — pool sees `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router]` → `true`.
7. The swap succeeds for `userB` despite them not being on the allowlist.

Conversely, if the admin only allowlists EOA addresses and not the router, step 6 evaluates `allowedSwapper[pool][router]` → `false`, and the swap reverts with `NotAllowedToSwap()` even for permissioned users routing through the standard path. [6](#0-5) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
