Audit Report

## Title
`SwapAllowlistExtension::beforeSwap` checks router address instead of originating user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates swaps by checking the `sender` argument forwarded from the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router — a necessary step for allowlisted users to use it — simultaneously opens the gate to every address on the internet.

## Finding Description
`MetricOmmPool::swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged via `abi.encodeCall` to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension::beforeSwap` then checks that forwarded `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap()` directly with no mechanism to thread the originating user's identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData   // user-controlled, extension ignores it
    );
```

The pool sees `msg.sender = router`. The extension evaluates `allowedSwapper[pool][router]`. The actual caller's identity (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never visible to any extension. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. There is no existing guard that recovers the originating user's address in the extension path.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to enforce KYC, regulatory, or institutional-only access is fully bypassed for any user who routes through the public `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps, receive output tokens from the pool at oracle prices, and drain LP value. This constitutes a direct loss of LP principal and a broken core pool invariant: the access-controlled swap policy is not enforced. This meets the "admin-boundary break" and "broken core pool functionality causing loss of funds" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any address can call `exactInputSingle`. The only precondition is that the pool admin has allowlisted the router — a step they must take if they want their own allowlisted users to be able to use the router at all. The bypass therefore activates automatically as soon as the pool is configured for normal router-mediated use. No special privileges, flash loans, or oracle manipulation are required.

## Recommendation
The extension must check the economically relevant actor, not the immediate caller of `pool.swap`. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires coordinated change to both the router and the extension.
2. **Transient storage "real sender"**: The router writes the originating user into a transient storage slot before calling `pool.swap`, and the extension reads that slot instead of the forwarded `sender` parameter.

The simplest safe rule: if `SwapAllowlistExtension` is configured on a pool, the router must never be allowlisted as a blanket entry. The extension must be redesigned so that router-mediated swaps carry the originating user's identity to the hook.

## Proof of Concept
```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension
ext.setAllowedToSwap(pool, alice, true);          // alice is KYC'd
ext.setAllowedToSwap(pool, address(router), true); // required for alice to use router

// bob (NOT allowlisted) bypasses the guard via the router
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
// extension sees sender = router (allowlisted), not bob — swap succeeds
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             pool,
        tokenIn:          token0,
        recipient:        bob,
        zeroForOne:       true,
        amountIn:         1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:    0,
        deadline:         block.timestamp + 1,
        extensionData:    ""
    })
);
// bob received token1 from a pool he is not allowed to trade on
```