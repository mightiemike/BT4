Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract, not the actual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every unprivileged user the ability to bypass the curated allowlist, completely defeating the pool's access-control invariant.

## Finding Description

**Root cause:** `MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
// ExtensionCalling.sol L160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct key), sender = router (wrong actor)
```

The lookup is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the real user anywhere:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData   // user-supplied, not injected with real caller
);
```

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly ignores the first (`sender`) argument and gates on `owner` — the LP position owner — which is preserved end-to-end. The swap extension has no equivalent "real actor" argument; it only receives `sender`, which is the router.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin calls `setAllowedToSwap(pool, router, true)` — the natural step to enable router-mediated swaps for allowlisted users.
3. Attacker (never individually allowlisted) calls `router.exactInputSingle(...)`.
4. Pool receives `pool.swap()` with `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Attacker executes unrestricted swap on the curated pool.

**Existing guards are insufficient:** There is no mechanism in the router or pool to encode the real originating user into the `sender` field or `extensionData` automatically. The `_setNextCallbackContext` in the router stores the payer for the callback but this is never forwarded to the extension.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (KYC'd market makers, whitelisted institutions, protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool's LP assets are exposed to unrestricted swaps from any caller, constituting a complete failure of the pool's core access-control invariant and a direct broken core pool functionality impact. LP principal is at risk from unrestricted swaps at oracle-derived prices.

## Likelihood Explanation

The only precondition is that the pool admin allowlists the router — a routine operational step for any pool intending to support the standard periphery. No privileged escalation, no malicious setup, no non-standard tokens, and no special timing are required. Any unprivileged user who observes the allowlist state (public mapping) can immediately exploit it by calling the public router.

## Recommendation

Pass the original user through the swap path so the extension can gate on the economically relevant actor. The cleanest fix is to have the router encode the real `msg.sender` into `extensionData` (or a dedicated field) before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that originator address instead of (or in addition to) `sender`. The router already uses `_setNextCallbackContext` to store the payer in transient storage; the same pattern can be used to inject the originator into `extensionData` automatically.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT individually allowlisted

// Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000,
        amountOutMinimum: 0,
        recipient:        attacker,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// Succeeds: pool.swap() called with msg.sender=router,
// extension checks allowedSwapper[pool][router] == true → passes,
// attacker swaps on curated pool despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
