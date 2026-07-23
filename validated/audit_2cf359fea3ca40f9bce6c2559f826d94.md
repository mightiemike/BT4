Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-Pool Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address rather than the actual end user. A pool admin who allowlists the router to support router-mediated swaps for approved users inadvertently grants every unprivileged user the ability to bypass the allowlist entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` inside the extension is the pool (correct), and `sender` is whoever called `pool.swap()`. In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when the user goes through the router
    recipient, ...
);
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly without forwarding the original caller's address to the extension via `extensionData`:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
);
```

The original `msg.sender` is stored in transient storage only for the payment callback (`_setNextCallbackContext`), never forwarded to the extension. So `sender` arriving at the extension is always the router address. The extension evaluates `allowedSwapper[pool][router]`, which is `true` if the admin allowlisted the router, and the swap proceeds regardless of who the actual caller is. The extension has no mechanism to inspect the original caller of the router.

## Impact Explanation
Any unprivileged user can bypass the swap allowlist on any pool that has allowlisted `MetricOmmSimpleRouter`. If the allowlist was deployed to protect LP funds from adverse selection (e.g., only trusted market makers may trade), the bypass allows arbitrary users to execute swaps at oracle-derived prices, draining LP value through unfavorable trades. The allowlist invariant — that only explicitly approved addresses may swap — is completely broken for all router-mediated swaps once the router is allowlisted. This constitutes a direct loss of LP principal and broken core pool functionality, meeting the contest's Critical/High/Medium impact threshold.

## Likelihood Explanation
Medium. The bypass requires the pool admin to have allowlisted the router address. This is a natural and expected configuration step: any admin who wants their allowlisted users to be able to use the standard periphery router must allowlist it. The admin has no way to achieve "allowlisted users can use the router" without simultaneously granting "all users can use the router," because the extension has no mechanism to inspect the original `msg.sender` of the router call. Once the router is allowlisted, the bypass is unconditional and requires no special privileges from the attacker.

## Recommendation
The `SwapAllowlistExtension` should gate on the economically relevant actor, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router passes the original `msg.sender` in `extensionData`; the extension decodes and checks it. This requires the router to be trusted to forward the correct address.
2. **Separate router-aware allowlist**: Maintain a mapping of trusted routers and, when `sender` is a known router, decode the actual user from `extensionData` before checking the allowlist.

The simplest safe default is to document that allowlisting the router grants access to all router users, and provide a router variant that forwards the original caller so the extension can check the correct identity.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, user1, true)` — intending only `user1` to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to let `user1` use the router.
4. `user2` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `user2`'s swap executes successfully, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
