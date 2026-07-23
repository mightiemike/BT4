The code confirms the claim at every step of the call chain:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`
2. `ExtensionCalling._beforeSwap()` forwards that `sender` to the extension
3. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` — where `sender` is the router, not the user
4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` to the pool

Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the guard by calling through the router.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` at line 231, passing the immediate caller as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // <-- router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` encodes and forwards this `sender` to the configured extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the user). `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the originating user's address, making the router the pool's `msg.sender` for all four swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). There is no mechanism in the pool or extension to recover the original user identity. The admin faces an impossible configuration: not allowlisting the router blocks all router-mediated swaps for everyone; allowlisting the router opens the pool to all users regardless of their individual allowlist status.

## Impact Explanation
This is an admin-boundary break. When the pool admin takes the natural action of allowlisting the router to enable router-mediated swaps for permitted users, the `SwapAllowlistExtension` guard is fully bypassed for all router-mediated swaps. Any unpermitted address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on a pool configured to be restricted, breaking the core pool access-control invariant. The pool's intended restriction on who may swap is rendered unenforceable via the public router path.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router address, which is the natural and expected action when the admin wants allowlisted users to be able to use the standard router. The admin has no way to distinguish "router called by an allowlisted user" from "router called by anyone" at the extension level, making the bypass an inevitable consequence of enabling router access. Any unpermitted user who knows the pool uses `SwapAllowlistExtension` can exploit this by routing through `MetricOmmSimpleRouter`.

## Recommendation
The pool should forward the original initiating user as `sender` to extensions rather than the immediate `msg.sender`. One approach: require the router to pass the user's address via `extensionData`, and have the extension decode and verify it against a registry of trusted routers (e.g., factory-registered routers). Alternatively, the pool can expose a `swapWithSender(address trustedSender, ...)` entry point restricted to factory-registered routers, forwarding `trustedSender` to hooks instead of `msg.sender`. The `DepositAllowlistExtension` should be audited for the analogous payer/owner separation issue in `MetricOmmPoolLiquidityAdder`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
2. Pool admin allowlists the router: `swapExtension.setAllowedToSwap(pool, address(router), true)` — intending to allow router-mediated swaps for permitted users only.
3. Non-allowlisted `attacker` calls `router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}))`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` to the pool is the router.
5. Pool calls `_beforeSwap(msg.sender=router, ...)` → `ExtensionCalling._beforeSwap` → `extension.beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → returns selector, no revert.
7. Attacker's swap executes on the restricted pool despite never being individually allowlisted.

Foundry test: deploy pool + extension + router, set `allowedSwapper[pool][router] = true`, prank as an address not in the allowlist, call `router.exactInputSingle(...)`, assert the swap succeeds (no `NotAllowedToSwap` revert). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
