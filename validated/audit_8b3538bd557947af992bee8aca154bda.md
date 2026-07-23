Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender`, allowing any user to bypass per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the end user. Any non-allowlisted user can bypass the per-user gate by routing through the public router if the router address is allowlisted, which is the only way to enable router-mediated swaps on the pool.

## Finding Description
The root cause is in `SwapAllowlistExtension.beforeSwap`:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is forwarded from `MetricOmmPool.swap` as `msg.sender` of the pool call. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router the `msg.sender` of `pool.swap()`. The pool then calls `_beforeSwap(msg.sender, ...)` passing the router address as `sender`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][userB]`.

Exact call path:
1. `userB` calls `MetricOmmSimpleRouter.exactInputSingle` — router becomes `msg.sender` for the pool call.
2. Router calls `pool.swap(recipient, ...)` — pool receives `msg.sender = router`.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passes router as `sender`.
4. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` — evaluates `true` because the router must be allowlisted for any router-mediated swap to work.
5. Swap executes for `userB` despite `userB` never being individually authorized.

Existing guards are insufficient: the extension has no mechanism to distinguish the true end user from an intermediary router. The `allowAllSwappers` bypass is a separate path and does not mitigate this.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to enforce KYC, regulatory, or protocol-specific access control loses that control entirely for any user routing through `MetricOmmSimpleRouter`. Unauthorized parties can execute swaps against LP positions in pools explicitly configured to restrict access, constituting broken core pool functionality with direct fund-impacting consequences — unauthorized actors can drain arbitrage value or execute swaps against LP positions the pool admin intended to exclude.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap entrypoint. The only precondition is that the pool admin has allowlisted the router address — which is the only operational way to enable router-mediated swaps on an allowlisted pool. Any non-allowlisted user who discovers the curated pool can trivially exploit this by calling `exactInputSingle` or any other router swap function. No privileged action is required from the attacker.

## Recommendation
The extension must identify the true end user, not the intermediary. Options:

1. **Pass the original caller in `extensionData`**: Have the router encode `msg.sender` (the end user) into `extensionData` and have the extension decode and verify that address against the allowlist, with a trusted-router registry to prevent spoofing.
2. **Trusted-forwarder registry**: The extension maintains a mapping of trusted routers; when `sender` is a trusted router, it requires the actual user identity to be supplied and verified via `extensionData`.
3. **Prohibit router allowlisting**: Document that the router must never be allowlisted on curated pools, and require all allowlisted users to call `pool.swap()` directly. This removes router usability for curated pools but eliminates the bypass.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension on beforeSwap hook
  - setAllowedToSwap(pool, router, true)       // required for router-mediated swaps
  - setAllowedToSwap(pool, userA, true)        // only userA is individually allowed
  - userB is NOT allowlisted

Attack:
  - userB calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(...) with msg.sender = router          [MetricOmmSimpleRouter.sol L72-80]
  - Pool calls _beforeSwap(router, ...)                           [MetricOmmPool.sol L230-231]
  - Extension checks allowedSwapper[pool][router] → true          [SwapAllowlistExtension.sol L37]
  - Swap executes for userB despite userB not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
