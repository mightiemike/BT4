Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged caller to bypass the per-user swap allowlist via the router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end-user. If the pool admin allowlists the router (a natural step to enable router-mediated swaps for permitted users), every unprivileged caller can bypass the per-user allowlist by routing through the public router.

## Finding Description

**Call path:**

1. `MetricOmmPool.swap()` captures `msg.sender` and passes it as `sender` to `_beforeSwap()`:
   ```
   _beforeSwap(msg.sender, recipient, ...)
   ```
2. `ExtensionCalling._beforeSwap()` ABI-encodes `sender` as the first argument and dispatches to every configured extension.
3. `SwapAllowlistExtension.beforeSwap(address sender, ...)` checks:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
       revert NotAllowedToSwap();
   }
   ```
   Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:
   ```solidity
   IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)
   ```
   So from the pool's perspective `msg.sender = router`, and `sender` forwarded to the extension is the **router address**, not the end-user.

**Exploit flow:**

- Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC-approved addresses.
- Admin allowlists individual approved users **and** the router (so approved users can use the standard periphery).
- Unprivileged attacker calls `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool.
- The pool passes `sender = router` to the extension; the extension finds `allowedSwapper[pool][router] == true` and allows the swap.
- Attacker successfully swaps on a pool they are not individually permitted to access.

**Why existing guards fail:**

The extension has no mechanism to look through the router to the originating EOA. The `onlyPool` modifier on the extension only verifies the caller is the registered pool; it does not validate the identity of the human behind the swap. The reentrancy guard and callback checks in the router are unrelated to allowlist enforcement.

## Impact Explanation

The swap allowlist is a core access-control feature. Bypassing it allows unprivileged traders to execute swaps on pools explicitly restricted to a defined set of counterparties. Depending on pool configuration this can expose LP capital to unintended counterparties, violate compliance requirements, and undermine the pool admin's ability to control who trades against the pool's liquidity. This constitutes broken core pool functionality and an admin-boundary break reachable by an unprivileged path (the public router).

## Likelihood Explanation

The condition is that the pool admin has allowlisted the router address. This is a realistic and expected operational step: any admin who wants approved users to be able to use the standard periphery must allowlist the router. Once that is done, the bypass is trivially repeatable by any caller with no special privileges, no tokens at risk, and no time constraint.

## Recommendation

The extension should not rely solely on the `sender` argument (the immediate pool caller). Two viable fixes:

1. **Pass the payer/originator through `extensionData`**: The router already forwards `extensionData` to the pool. Require allowlisted pools to include a signed or router-attested end-user address in `extensionData`, and verify it in the extension.
2. **Check recipient instead of (or in addition to) sender**: For swap allowlists the economically relevant identity is often the recipient. Gate on `recipient` or require both `sender` and `recipient` to be allowlisted.
3. **Allowlist-aware router**: Add a dedicated router path that embeds the originating `msg.sender` in `extensionData` so the extension can verify the true end-user.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension; router is allowlisted, attacker is not.
// 1. Admin setup
swapAllowlist.setAllowedToSwap(pool, address(router), true);
// attacker address is NOT allowlisted

// 2. Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Swap succeeds; attacker traded on a restricted pool without being individually allowlisted.
```