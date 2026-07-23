Audit Report

## Title
SwapAllowlistExtension checks router address instead of originating user, allowing allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router (the only way to let allowlisted users access the router) simultaneously grants unrestricted swap access to every user of that shared router contract, completely defeating the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // pool's msg.sender — the router when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to the configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called the pool. When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the router:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The check therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router address is a single shared contract; allowlisting it grants access to every caller of the router. The `DepositAllowlistExtension` avoids this flaw by checking `owner` (the position holder) rather than `sender` (the caller), but `SwapAllowlistExtension` has no equivalent correct fallback.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — alice is KYC'd.
3. Admin calls `setAllowedToSwap(pool, router, true)` — required for alice to use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(bob, ...)` → pool's `msg.sender = router`.
6. Extension checks `allowedSwapper[pool][router] = true` → passes.
7. Bob's swap executes against the restricted pool.

## Impact Explanation
Any user can swap against a pool the admin intended to restrict to a specific allowlist, provided the router is allowlisted. This breaks the core invariant of `SwapAllowlistExtension`. For pools using oracle-anchored prices intended only for privileged counterparties (e.g., KYC'd users), non-allowlisted users can trade at those prices, constituting a direct loss of LP assets. This meets the Sherlock threshold for High severity: direct loss of LP principal through a broken core pool access-control mechanism.

## Likelihood Explanation
The bypass requires the pool admin to allowlist the router. This is the only way to let allowlisted users access slippage protection or multi-hop swaps via the router — without it, allowlisted users' direct addresses are not the router and cannot use it. Any pool admin who deploys a swap-allowlisted pool and also wants allowlisted users to use the router will trigger this condition. The pool admin has no way to simultaneously allow allowlisted users to use the router and block non-allowlisted users from doing the same, because the router is a shared contract with a single address. The condition is natural, expected, and has no safe workaround in the current design.

## Recommendation
The extension must check the identity of the actual human swapper, not the intermediary contract. Two viable approaches:

1. **Router-forwarded identity via `extensionData`:** Modify `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` before calling the pool, and update `SwapAllowlistExtension.beforeSwap` to decode and verify that field when `sender` is a known/trusted router address. Requires a trusted router registry in the extension.

2. **Separate router-aware extension variant:** Provide a `SwapAllowlistExtension` variant that checks `sender` for direct calls and falls back to an `extensionData`-encoded user identity for router calls, with clear documentation that the router must be trusted to forward the correct originator.

## Proof of Concept
```
// Foundry test outline
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (BEFORE_SWAP_ORDER set)
  swapExtension.setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  swapExtension.setAllowedToSwap(pool, router, true)  // to let alice use the router
  // Add liquidity as alice

Attack:
  vm.prank(bob);  // bob is NOT allowlisted
  router.exactInputSingle(ExactInputSingleParams({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: 1000,
      ...
  }));

Expected: revert NotAllowedToSwap
Actual:   swap succeeds — allowedSwapper[pool][router] = true passes the check
```