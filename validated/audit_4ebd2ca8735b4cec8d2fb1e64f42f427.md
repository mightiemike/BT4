Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract — not the end user. When a pool admin allowlists the router to permit router-mediated swaps for legitimate users, every public caller of the router automatically passes the gate, defeating the allowlist entirely. The pool admin has no mechanism to simultaneously allow router-mediated swaps for specific users and block unauthorized users.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← the router when called via MetricOmmSimpleRouter
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)`. The pool's `msg.sender` is the router, so `sender` arriving at the extension is the router's address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The router does store `msg.sender` in transient storage for callback purposes (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but this value is never forwarded to the extension — it is only used internally for the payment callback. There is no mechanism in the current architecture to pass the real initiator to the extension.

**Exploit path:**
1. Pool is configured with `SwapAllowlistExtension`
2. Admin calls `setAllowedToSwap(pool, alice, true)` — alice is the only intended swapper
3. Admin calls `setAllowedToSwap(pool, router, true)` — so alice can use the router
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`
5. Router calls `pool.swap(...)` with `msg.sender = router`
6. Extension checks `allowedSwapper[pool][router] == true` → passes
7. Bob successfully swaps against the restricted pool

The pool admin cannot avoid this: if they do NOT allowlist the router, even alice cannot use the router. If they DO allowlist the router, all public users of the router bypass the gate. There is no intermediate option.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to specific users (KYC'd counterparties, whitelisted market makers) cannot enforce that restriction when users interact through `MetricOmmSimpleRouter`. Any unauthorized user can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` and the extension will pass them because it sees the router address. Unauthorized users can swap against LP positions at oracle-quoted prices, causing direct loss of LP principal. This is a broken core pool functionality (allowlist curation) causing direct loss of funds.

## Likelihood Explanation
The router is the primary documented user-facing entry point. Any pool admin who configures a swap allowlist and also wants their allowlisted users to use the router will inevitably allowlist the router, triggering the bypass. The attacker needs no special privilege — a single public call to `MetricOmmSimpleRouter.exactInputSingle` suffices. The condition is reachable on every allowlisted pool that also permits router access.

## Recommendation
The extension must check the economically relevant actor — the end user — not the intermediary. Two options:

1. **Pass the original initiator through the hook.** The router already stores `msg.sender` in transient storage at entry (`_setNextCallbackContext`). The pool could read and forward an `initiator` field alongside `sender` to extensions. The extension would gate on `initiator` when `sender` is a known router.

2. **Require the router to encode the real user in `extensionData`.** The router would encode `msg.sender` into `extensionData`, and the extension would decode and check it when `sender` is the router address. This is opt-in per pool and requires no core changes.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  Execution path:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
          msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              allowedSwapper[pool][router] == true  ✓  (passes!)
        → swap executes, bob receives token1 output

Result: bob, who is not on the allowlist, successfully swaps against the restricted pool.

Foundry test sketch:
  1. Deploy pool with SwapAllowlistExtension
  2. setAllowedToSwap(pool, alice, true)
  3. setAllowedToSwap(pool, address(router), true)
  4. vm.prank(bob); router.exactInputSingle(...)
  5. Assert: swap succeeds (no NotAllowedToSwap revert)
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
