Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the immediate caller (`sender`) rather than the originating user, allowing any unprivileged caller to bypass a curated pool's per-user allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for their permitted users inadvertently grants every unprivileged caller the ability to bypass the per-user allowlist entirely, breaking the curated-pool invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` stores the originating `msg.sender` in transient storage for payment callback purposes only (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), but calls `pool.swap()` directly with `""` as `extensionData`, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData  // originating user address is NOT forwarded here
    );
```

The originating user's address is never forwarded to the extension. The extension sees `sender = router_address`. If the pool admin has allowlisted the router (the necessary step to enable router-mediated swaps for their permitted users), the check `allowedSwapper[pool][router] == true` passes for every caller regardless of their individual allowlist status. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional partners, or protocol-controlled addresses) loses that restriction entirely once the router is allowlisted. LP providers who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted adverse selection and unintended counterparty risk. This constitutes an admin-boundary break where the configured allowlist guard is bypassed by an unprivileged path, and broken core pool functionality since the allowlist extension's primary purpose is rendered ineffective.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants their allowlisted users to be able to use the router (the standard UX path) must allowlist the router address. This is the expected operational pattern, making the bypass condition highly likely to be triggered in production deployments of curated pools. The attacker requires no special privileges — any EOA or contract can call `router.exactInputSingle()`.

## Recommendation
The `SwapAllowlistExtension` must gate by the originating user, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Pass the originating user through the router**: `MetricOmmSimpleRouter` should forward `msg.sender` as a verified field in `extensionData` (e.g., ABI-encoded), and `SwapAllowlistExtension` should decode and check that value instead of `sender` when `sender` is a known router.

2. **Transient storage query interface**: The router already stores the originating `msg.sender` in transient storage via `_setNextCallbackContext`. Expose it via a standard interface that the extension can query during `beforeSwap`, so the allowlist check always operates on the economic actor, not the intermediary.

The simplest correct fix is option 1: encode `msg.sender` into `extensionData` in the router and have the extension decode and verify it, so the allowlist check always reflects the true initiating user.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is permitted.
3. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls:
   ```solidity
   router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
       pool: address(pool),
       tokenIn: token0,
       ...
       extensionData: ""
   }));
   ```
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, recipient, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → **passes**.
8. Bob's swap executes successfully against the curated pool, bypassing the per-user allowlist.

A Foundry integration test can confirm this by asserting that Bob's `exactInputSingle` call succeeds (does not revert with `NotAllowedToSwap`) when the router is allowlisted, even though Bob's address is not in `allowedSwapper`.