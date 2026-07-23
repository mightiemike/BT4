Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted on a curated pool, any unprivileged user can bypass the per-user swap allowlist by routing through the public router.

## Finding Description

**Root cause — identity mismatch between economic actor and checked actor:**

In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol:162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Router path — sender is the router, not the user:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

The router never passes the original `msg.sender` (the actual user) to the pool. The pool only sees the router address.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific counterparties.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can reach the pool via the public router.
3. An unprivileged user (not in the allowlist) calls `router.exactInputSingle(...)`.
4. The router calls `pool.swap(...)` — pool's `msg.sender` = router.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → no revert.
6. The non-allowlisted user's swap executes successfully, bypassing the intended access control.

**Existing guards are insufficient:** The `onlyPool` modifier on `beforeSwap` only verifies the caller is a registered pool; it does not recover the original user identity. There is no mechanism in the router or pool to thread the original `msg.sender` through to the extension.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., institutional traders, KYC'd users, or whitelisted market makers) can be accessed by any unprivileged user via the public router. This constitutes an admin-boundary break: the pool admin's access control policy is bypassed by an unprivileged path. Depending on pool configuration, unauthorized swappers can extract value from LPs at oracle-anchored prices, causing direct loss of LP principal.

## Likelihood Explanation
The bypass is unconditionally reachable by any unprivileged user as long as the router is allowlisted on the target pool. The router is a public, permissionless contract. No special capability, flash loan, or privileged role is required. The attacker only needs to call `exactInputSingle` (or any other router swap function) targeting the restricted pool. The condition — router being allowlisted — is a natural operational state: pool admins who want allowlisted users to use the router must allowlist it, inadvertently opening the bypass to everyone.

## Recommendation
The `SwapAllowlistExtension` must gate on the true economic actor, not the intermediary. Two complementary fixes:

1. **Extension-side:** Accept the original user identity via `extensionData` and verify it against the allowlist, combined with a signature or trusted-forwarder pattern. Alternatively, document that the extension is incompatible with router-mediated swaps and enforce this at the pool level.

2. **Router-side:** Thread the original `msg.sender` through to the pool as a verifiable parameter (e.g., packed into `extensionData` with a router-signed attestation), so the extension can check the real user.

3. **Simplest safe fix:** Remove the router from the allowlist and require allowlisted users to call `pool.swap()` directly, accepting that the router cannot be used with allowlisted pools.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Add liquidity so pool has depth
    vm.prank(lp);
    pool.addLiquidity(lp, 0, deltas, "", "");

    // Attacker routes through the public router — bypass succeeds
    vm.prank(attacker);
    token0.approve(address(router), type(uint256).max);
    // Should revert NotAllowedToSwap but does NOT
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            recipient: attacker,
            deadline: block.timestamp + 1,
            zeroForOne: true,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // Attacker received token1 despite not being on the allowlist
}
```