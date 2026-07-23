Looking at the key code paths:

**`MetricOmmPool.swap`** passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap`** checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the argument (i.e., `msg.sender` of `pool.swap`): [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle`** calls `pool.swap(...)` directly — making the router the `msg.sender` of `pool.swap`: [3](#0-2) 

The vulnerability is real and confirmed. The extension checks the router address, not the actual user.

---

Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Enabling Allowlist Bypass via Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap`. When users route through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap`, so the extension checks the router's allowlist status rather than the actual user's. If the router is allowlisted (a natural admin action to enable router usage), any non-allowlisted user can bypass the swap allowlist by routing through it.

## Finding Description
**Root cause:** In `MetricOmmPool.swap` (line 231), `_beforeSwap(msg.sender, ...)` is called, passing the direct caller of `pool.swap` as `sender`. In `SwapAllowlistExtension.beforeSwap` (line 37), the check is `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][caller_of_pool_swap]`.

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to approved addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to permit router-mediated swaps (a natural action so allowlisted users can use the router).
3. Attacker (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient, ...)` — router is now `msg.sender` of `pool.swap`.
5. Pool calls `_beforeSwap(router_address, ...)`.
6. Extension checks `allowedSwapper[pool][router_address]` → `true` → swap proceeds.
7. Attacker successfully swaps despite not being individually allowlisted.

**Why existing guards fail:** The `onlyPool` guard in `BaseMetricExtension` only ensures the extension is called by a registered pool — it does not verify that `sender` represents the actual end user. There is no mechanism to unwrap the router's `msg.sender` to recover the originating EOA.

**Second broken scenario:** If the pool admin does NOT allowlist the router, individually allowlisted EOAs cannot use the router at all (their EOA address is not `msg.sender` of `pool.swap` when routing), breaking legitimate usage.

## Impact Explanation
The swap allowlist — a core access-control mechanism for permissioned pools — can be fully bypassed by any unprivileged user via the public `MetricOmmSimpleRouter`. This constitutes broken core pool functionality: the allowlist fails to gate the actual swapper identity, allowing unauthorized swaps to execute against pool liquidity. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds or unusable swap flows" impact categories. Severity: High.

## Likelihood Explanation
The bypass requires only that the router address is allowlisted on the pool — a natural and expected admin action for any pool that intends to support router-mediated swaps. The attacker needs no special privileges, no capital beyond swap input, and the path is fully reachable via the public `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` / `exactOutputSingle` entry points. Repeatable at will once the router is allowlisted.

## Recommendation
The extension should check the originating user rather than the direct pool caller. Options:
1. **Pass the real user through extensionData:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires router cooperation and trust.
2. **Check `tx.origin` as a fallback:** Not recommended due to composability issues.
3. **Preferred fix:** Redesign the allowlist to gate on `recipient` or require the router to pass the real user's address in a verified way (e.g., a signed permit or a trusted forwarder pattern). Alternatively, document that the allowlist only supports direct pool calls and that the router must not be allowlisted on permissioned pools.

## Proof of Concept
```solidity
// Foundry test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, router allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // attacker is NOT individually allowlisted
    
    // Attacker calls router (not pool directly)
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: address(token0),
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
