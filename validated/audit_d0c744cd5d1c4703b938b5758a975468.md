Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the end-user, making per-user swap allowlists permanently bypassable through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router address, so the check resolves to `allowedSwapper[pool][router]` — the actual end-user's identity is never examined. This produces two mutually exclusive failure modes: allowlisted users are locked out of the router, or if the admin allowlists the router to restore access, every non-allowlisted user can bypass the guard entirely.

## Finding Description
In `MetricOmmPool.swap`, `msg.sender` is passed directly as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-231
_beforeSwap(
  msg.sender,   // router address when called via MetricOmmSimpleRouter
  ...
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The lookup is `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with no mechanism to forward the originating user's address:

```solidity
// MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // SwapAllowlistExtension does not read this
  );
```

The `extensionData` field is forwarded but `SwapAllowlistExtension.beforeSwap` ignores it entirely (the parameter is unnamed/discarded). There is no existing path to convey user identity through the router.

**Failure mode A:** Admin allowlists `userA`. Direct `pool.swap()` succeeds (`sender = userA`). Router call reverts (`sender = router`, `allowedSwapper[pool][router] = false`). The router is permanently unusable for any allowlisted user.

**Failure mode B:** Admin allowlists the router to restore router access (`allowedSwapper[pool][router] = true`). Now any address — including those never allowlisted — can call `router.exactInputSingle(...)` and the check passes unconditionally, because it resolves to `allowedSwapper[pool][router]` regardless of the actual caller.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`, all of which call `pool.swap()` directly without forwarding the originating user.

## Impact Explanation
`SwapAllowlistExtension` is the protocol's only per-pool swap access-control primitive. In failure mode A, allowlisted users are forced to interact directly with the pool contract, bypassing slippage protection, multi-hop routing, and deadline enforcement provided by the router — a broken core swap flow. In failure mode B, the guard is fully neutralised: any address can swap in a pool the admin intended to restrict. If the allowlist was deployed to exclude adversarial or non-KYC'd traders, those traders can drain LP value through informed swaps, directly impacting LP principal. Both outcomes meet the "broken core pool functionality" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
Failure mode A is triggered by the default correct configuration (allowlist individual users, not the router) and affects every pool that deploys `SwapAllowlistExtension` and expects users to use the router. Failure mode B is triggered when the pool admin takes the natural remediation step of allowlisting the router — a reasonable and expected admin action with no documentation warning against it. No special attacker capability is required; any EOA can exploit failure mode B by simply calling the router.

## Recommendation
`SwapAllowlistExtension` must gate the actual end-user, not the intermediary. Two viable approaches:

1. **Signed identity in `extensionData`**: Have the router embed `msg.sender` (the actual user) in `extensionData` and have `SwapAllowlistExtension` decode and verify it (with a trusted-router flag or signature).
2. **Router-aware forwarding**: Add a `swapWithSender(address actualSender, ...)` entry point on the pool (callable only by whitelisted routers) that passes `actualSender` instead of `msg.sender` to extension hooks.
3. **Separate router allowlist**: Distinguish between "router is allowed to relay" and "user is allowed to trade" by checking both `allowedSwapper[pool][router]` (relay permission) and a user identity extracted from `extensionData`.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
// 2. Admin allowlists userA only.
swapExt.setAllowedToSwap(address(pool), userA, true);

// 3. userA swaps directly — succeeds (sender = userA, allowedSwapper[pool][userA] = true).
vm.prank(userA);
pool.swap(userA, false, 1000, type(uint128).max, "", "");

// 4. userA swaps via router — REVERTS (sender = router, allowedSwapper[pool][router] = false).
vm.prank(userA);
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: userA, ...}));
// → NotAllowedToSwap

// 5. Admin allowlists the router to fix userA's access.
swapExt.setAllowedToSwap(address(pool), address(router), true);

// 6. Non-allowlisted userB now bypasses the guard via router — SUCCEEDS.
vm.prank(userB);  // userB was never allowlisted
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: userB, ...}));
// → swap executes; allowlist is fully bypassed
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
