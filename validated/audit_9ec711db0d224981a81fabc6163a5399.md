Audit Report

## Title
Swap Allowlist Bypass via Router: `SwapAllowlistExtension` checks `sender` (the router) instead of the originating user - (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `swap()` call — the router contract, not the end user. When a disallowed user routes through `MetricOmmSimpleRouter`, the extension checks the router's address against the allowlist instead of the user's address. If the router is allowlisted (or if the pool admin allowlists the router to enable router-based swaps for any allowed user), any disallowed user can bypass the swap allowlist by calling through the router.

## Finding Description

**Call chain:**

1. User (not on allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — here `msg.sender` to the pool is the **router address**.
3. Pool's `swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender=router, ...)` to `SwapAllowlistExtension`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]`.

If the router is in the allowlist (which is necessary for any router-mediated swap to work on an allowlisted pool), **every user** who can call the router can bypass the per-user allowlist gate, because the check is on the router address, not the originating EOA.

The root cause is in `SwapAllowlistExtension.beforeSwap`:
```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```
`sender` here is the pool's `msg.sender` — the router — not the originating user. The pool passes `msg.sender` (the router) as `sender` to the extension hook. There is no mechanism in the hook or the pool to recover the original EOA.

**Existing guards are insufficient:** The `onlyPool` modifier on `beforeSwap` only ensures the pool is calling, not that the correct actor is being checked. The pool correctly passes its own `msg.sender` as `sender`, but that is the router, not the end user.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict swaps to a known set of addresses can be bypassed by any user routing through `MetricOmmSimpleRouter`. The disallowed user executes a live swap against the pool, receiving output tokens and paying input tokens — a direct policy bypass enabling unauthorized trading on a restricted pool. This constitutes broken core pool functionality (allowlist gate failure) and admin-boundary break where an unprivileged trader bypasses a configured access control.

## Likelihood Explanation
Any user who can call the public `MetricOmmSimpleRouter` can exploit this. The only precondition is that the router address is allowlisted on the pool (which is required for any router-mediated swap to work). This is a straightforward, repeatable bypass requiring no special privileges, no malicious setup, and no off-chain manipulation. It is reachable on every allowlisted pool that permits router access.

## Recommendation
The `SwapAllowlistExtension` should gate on the **originating user**, not the immediate `msg.sender` of the pool's `swap()` call. Options:
- Pass the originating user through `extensionData` and verify it in the extension (requires router cooperation and trust).
- Require direct pool calls only (no router intermediary) for allowlisted pools — document this constraint clearly.
- Redesign the allowlist to gate on `recipient` instead of `sender` if the recipient is always the end user, or add a separate field for the originating caller in the extension interface.
- Alternatively, the pool could expose a mechanism for the router to attest the originating caller in a verifiable way (e.g., via transient storage read by the extension).

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; router is allowlisted, attacker is not.
// swapExtension.setAllowedToSwap(pool, address(router), true);
// attacker is NOT in allowedSwapper[pool]

// Attacker calls router directly:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp + 1,
    extensionData: ""
}));
// Pool calls _beforeSwap(sender=router, ...)
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
// Attacker's swap executes despite not being on the allowlist
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router address, attempt `exactInputSingle` from an address not in the allowlist, assert the swap succeeds (demonstrating the bypass).