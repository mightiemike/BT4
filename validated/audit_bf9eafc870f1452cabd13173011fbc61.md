Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates by Caller (Router) Instead of User Identity, Enabling Full Allowlist Bypass or Blocking Legitimate Users - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the router contract address when swaps are routed through `MetricOmmSimpleRouter`. This means the allowlist gates the intermediary contract, not the actual swapper. A pool admin who allowlists the router to enable standard user access inadvertently opens the pool to every user. Conversely, a pool admin who allowlists specific EOA addresses finds those users permanently blocked from the standard periphery.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` passed to the extension is the router address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This is structurally opposite to `DepositAllowlistExtension.beforeAddLiquidity`, which correctly gates by `owner` (the second parameter — the actual beneficiary), allowing `MetricOmmPoolLiquidityAdder` to work transparently while still enforcing per-user deposit restrictions:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

**Bypass path:** Pool admin calls `setAllowedToSwap(pool, router, true)` to enable standard user access. Once the router is allowlisted, any user — including those the admin intended to block — can call `router.exactInputSingle(...)` and pass the check, because the extension only sees the router address.

**Blocking path:** Pool admin calls `setAllowedToSwap(pool, alice, true)` to allow only Alice. Alice calls `router.exactInputSingle(...)`. The extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert. Alice is blocked from the standard periphery despite being explicitly allowlisted.

## Impact Explanation

**Guard bypass (High):** If the pool admin allowlists the router to enable standard user access, the `SwapAllowlistExtension` guard is completely bypassed for all users. Any address can execute swaps on a pool intended to be restricted, accessing oracle-anchored liquidity without authorization. This is broken core pool functionality — the allowlist extension provides no protection when the router is allowlisted.

**Broken core swap functionality (Medium):** If the pool admin allowlists specific user addresses (the natural and documented use case), those users cannot use the standard `MetricOmmSimpleRouter` periphery at all. The swap path is unusable for the intended participants, constituting broken core swap functionality.

## Likelihood Explanation

The `DepositAllowlistExtension` gates by `owner` (user identity), creating a strong natural expectation that `SwapAllowlistExtension` behaves symmetrically. A pool admin configuring both extensions will almost certainly allowlist user addresses in the swap extension, immediately triggering the blocking path. Alternatively, a pool admin who allowlists the router to "enable normal swaps" triggers the bypass path. Both are reachable through normal, non-malicious configuration without any attacker privilege. The `setAllowedToSwap` setter is callable by any pool admin, and both misconfiguration paths are the obvious, expected usage of the extension.

## Recommendation

Mirror the `DepositAllowlistExtension` pattern: check the `recipient` argument (second parameter, currently ignored with `address,`) rather than `sender`. The `recipient` is the actual beneficiary of the swap and is set by the original caller, not the intermediary router. Alternatively, if gating by the calling contract (router) is the intended design, document this explicitly and rename the setter/mapping to reflect that it gates routers/contracts, not users. If per-user gating is desired, the pool must pass the original user identity through `extensionData` and the extension must decode it.

## Proof of Concept

**Bypass path:**
1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps.
3. Non-allowlisted user `bob` calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(msg.sender=router, ...)`.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Bob, never individually allowlisted, successfully swaps on a restricted pool.

**Blocking path:**
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allow only Alice.
2. Alice calls `router.exactInputSingle({pool: pool, ...})`.
3. Extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap` revert.
4. Alice is blocked from the standard periphery despite being explicitly allowlisted.