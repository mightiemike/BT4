Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently grants swap access to every address on the network, completely neutralising the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making `pool.msg.sender = router`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`. The actual end user's address is never seen by the extension. The same structural problem exists for multi-hop `exactInput`, where intermediate hops use `address(this)` (the router) as the effective caller: [4](#0-3) 

There is no mechanism in the extension or the pool's call path to recover the original `msg.sender` of the router call. The `extensionData` field is passed through unchanged but the extension does not decode it.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to be a permissioned venue — to enforce KYC, prevent arbitrage bots, or restrict trading to protocol-controlled addresses. Once the router is allowlisted (the natural and expected step to let approved users trade via the standard periphery UX), the guard is completely neutralised. Any address can execute swaps against the pool's LP positions, exposing LPs to the full range of adversarial trading (arbitrage, sandwich, directional pressure) that the allowlist was designed to prevent. This is a direct loss path for LP principal, constituting a broken core pool functionality and admin-boundary bypass by an unprivileged path.

## Likelihood Explanation
The scenario is reachable by any unprivileged user. The only precondition is that the pool admin has allowlisted the router — a step that is both natural and expected for any pool that wants to support the standard periphery UX. The admin has no way to simultaneously allow router-mediated swaps and enforce per-user identity checks with the current extension design. There is no warning in the extension or its interface that allowlisting the router collapses the per-user gate. The attack is repeatable and requires no special privileges.

## Recommendation
`SwapAllowlistExtension` must check the economic actor (the end user), not the transport layer (the router). Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData`; the extension decodes and checks that address. Pool admins allowlist end-user addresses, not the router.
2. **Two-level check**: Add a separate router allowlist; allowlisted routers may relay swaps, but only on behalf of allowlisted end users encoded in `extensionData`.

Either way, `beforeSwap` must not treat the router address as the identity to gate. [2](#0-1) 

## Proof of Concept
```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension as EXTENSION_1.
2. Admin calls setAllowedToSwap(pool, userA, true)    // intended user
3. Admin calls setAllowedToSwap(pool, router, true)   // to let userA use the router

Attack
──────
4. userB (not allowlisted) calls:
       router.exactInputSingle({pool: pool, …, recipient: userB})

5. Router executes (MetricOmmSimpleRouter.sol L72-80):
       pool.swap(userB, …)   // msg.sender = router

6. Pool calls _beforeSwap(msg.sender=router, …) (MetricOmmPool.sol L230-240)

7. Extension evaluates (SwapAllowlistExtension.sol L37):
       allowedSwapper[pool][router] == true  ✓

8. Swap settles. userB receives output tokens.
   The per-user allowlist was never consulted for userB.
``` [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-19)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
