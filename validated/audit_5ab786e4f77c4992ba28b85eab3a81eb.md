Audit Report

## Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Allowing Allowlist Bypass or DoS via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking whether `sender` is allowlisted, but when users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address rather than the original user. This produces two distinct failure modes: if the router is allowlisted, any user bypasses the allowlist; if only user EOAs are allowlisted, all router-mediated swaps revert, breaking the primary swap path.

## Finding Description
In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← pool's msg.sender = router when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged to the extension via `abi.encodeCall`. The extension then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router address (wrong — should be the original user). When `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` at line 72-80, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router_address]` instead of `allowedSwapper[pool][original_user]`.

The pool's `swap()` interface accepts no explicit `sender` parameter — only `recipient`, `zeroForOne`, `amountSpecified`, `priceLimitX64`, `callbackData`, and `extensionData` — so there is no mechanism for the router to forward the original caller's identity through the standard call path.

**Bypass mode:** Pool admin allowlists the router address to support router-mediated swaps. Every user, including those the admin intended to exclude, can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The extension sees the allowlisted router and passes unconditionally.

**DoS mode:** Pool admin allowlists only specific user EOAs (not the router). Every allowlisted user's router swap reverts because the extension sees the non-allowlisted router address, breaking the primary periphery entry point.

## Impact Explanation
**Bypass:** Unauthorized users gain access to a restricted pool. If the pool holds liquidity priced at oracle rates, unauthorized swappers can extract value from LPs, causing direct LP principal loss. This meets the "direct loss of user principal" threshold.

**DoS:** Core swap functionality is broken for all allowlisted users who use the router. The primary user-facing swap interface becomes unusable for the exact users the admin intended to serve. This meets the "broken core pool functionality causing loss of funds or unusable swap flows" threshold.

## Likelihood Explanation
Any pool deploying `SwapAllowlistExtension` is structurally affected — the identity mismatch is not conditional on any edge case. The bypass requires only that the router be allowlisted, which is the natural configuration for a pool that wants to support router-mediated swaps while restricting direct callers. The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle` with no special role or setup.

## Recommendation
The pool's `swap()` function should accept an explicit `sender` parameter (analogous to how `addLiquidity` separates `sender` from `owner`), allowing the router to forward the original caller's address. Alternatively, the `SwapAllowlistExtension` could decode the original caller from a trusted field in `extensionData`, with the router injecting it — though this requires the extension to trust the router's encoding. The cleanest fix is a first-class `sender` parameter on `pool.swap()`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router_address, true)` to enable router-mediated swaps; does **not** allowlist `attacker_address`.
3. `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router_address, ...)` at `MetricOmmPool.sol` L230.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router_address]` → **true**.
7. Attacker's swap executes against the restricted pool, bypassing the intended access control.

Conversely, if the admin allowlists only `user_address` (not the router), step 6 evaluates `allowedSwapper[pool][router_address]` → **false**, and the legitimate allowlisted user's router swap reverts.