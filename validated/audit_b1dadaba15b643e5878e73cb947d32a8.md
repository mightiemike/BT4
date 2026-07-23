Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's caller is the router, so the extension checks the router address rather than the originating user. Any pool admin who allowlists the router to enable legitimate router-mediated swaps inadvertently opens the gate to every user on the internet.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension (L149-177). `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()` — the router, not the end user.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to forward the original `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← user-supplied, unverified
  );
```

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The `extensionData` field is passed through from the user's params but there is no signing, verification, or trusted-forwarder mechanism in the extension to authenticate the original caller.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists `allowedUser` and also allowlists the router so `allowedUser` can use it.
2. `bannedUser` calls `MetricOmmSimpleRouter.exactInputSingle(pool=..., ...)`.
3. Router calls `pool.swap(...)` with `msg.sender = router`.
4. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. `bannedUser` successfully swaps on a pool they were explicitly excluded from.

**Existing guards are insufficient:** The only guard is `allowedSwapper[msg.sender][sender]` in the extension. There is no secondary check on the originating user, no trusted-forwarder pattern, and no factory-level enforcement preventing `SwapAllowlistExtension` from being paired with `MetricOmmSimpleRouter`.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The attacker can execute swaps at oracle-anchored prices, extracting value at the expense of LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal and a complete failure of the pool's access-control invariant — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break by an unprivileged path" allowed impacts.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical public periphery contract. Any user aware of the router address can exploit this. No special privileges, flash loans, or multi-step setup are required. The only precondition is that the pool admin has allowlisted the router, which is the natural and expected operational step to enable router-mediated swaps for legitimate users. Likelihood is **High**.

## Recommendation
1. **Router-level:** Have `MetricOmmSimpleRouter` store the originating `msg.sender` in transient storage (analogous to how it already stores the payer via `_setNextCallbackContext`) and expose it via a read function that extensions can call back into during `beforeSwap`.
2. **Extension-level:** `SwapAllowlistExtension.beforeSwap` should accept an `extensionData` payload carrying the verified original sender, authenticated by the router (e.g., via a trusted-forwarder registry), and verify it against the allowlist instead of the raw `sender` argument.
3. **Simplest short-term fix:** Document that `SwapAllowlistExtension` is incompatible with `MetricOmmSimpleRouter` and enforce this at the factory level by reverting pool creation that pairs both.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is on the allowlist.
// Pool admin also allowlists the router so allowedUser can use it.
swapExt.setAllowedToSwap(address(pool), address(router), true);
swapExt.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: bannedUser routes through the router.
vm.startPrank(bannedUser);
token0.approve(address(router), type(uint256).max);
// This succeeds because the extension sees sender=router, which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bannedUser,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// bannedUser successfully swapped on a pool they were explicitly excluded from.
```