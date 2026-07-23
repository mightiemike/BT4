Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. If the pool admin allowlists the router address to support router-mediated swaps for permitted users, every unprivileged user can bypass the allowlist by calling the router, because the extension sees only the router's address and not the originating EOA.

## Finding Description

**Call path through the router:**

1. Unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = user EOA.
2. Router calls `pool.swap(params.recipient, ...)` — `msg.sender` at the pool = router address.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passes the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = router. The check resolves to `allowedSwapper[pool][router]`.

**Root cause:** The pool passes its own `msg.sender` (the immediate caller) as the `sender` argument to every extension hook. When the immediate caller is the router, the extension cannot distinguish between different users behind the router. The allowlist therefore gates on the router contract address, not on the originating user.

**Why the prerequisite is realistic:** A pool admin who wants allowlisted users to be able to use the standard router UX must add the router to the allowlist (`setAllowedToSwap(pool, router, true)`). Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller, regardless of who initiated the transaction.

**Existing guards are insufficient:** `BaseMetricExtension.onlyPool` only verifies that the caller of the extension is a registered pool — it does not verify the identity of the original user. There is no mechanism in the hook interface to recover the original EOA from the router context.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps at oracle-anchored prices against LP liquidity, causing direct LP losses whenever oracle prices are favorable to the swapper. This is an admin-boundary break: the pool admin's allowlist policy is bypassed by an unprivileged public path.

## Likelihood Explanation

The bypass requires the router to be allowlisted, which is a realistic and expected operational step for any curated pool that wants to support standard periphery UX. Once that step is taken, the bypass is trivially repeatable by any unprivileged address with no special capability. The router is a public, permissionless contract.

## Recommendation

Pass the original user identity through the router to the pool, or redesign the allowlist to check the recipient or a user-supplied identity field rather than the immediate pool caller. One concrete approach: add an `originator` field to the swap extension data that the router populates with `msg.sender`, and have the extension verify that field instead of (or in addition to) the `sender` argument. Alternatively, the pool could expose a separate `swapOnBehalf(address originator, ...)` entry point that the router uses, and the extension checks `originator`.

## Proof of Concept

```solidity
// Setup
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool admin allowlists the router so that permitted users can use it
ext.setAllowedToSwap(pool, address(router), true);
// Alice is also individually allowlisted for direct calls
ext.setAllowedToSwap(pool, alice, true);

// Exploit: Bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    tokenIn: token1,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob's swap succeeds; allowedSwapper[pool][router] == true passes the check
```