Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` binds to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address to enable router-mediated swaps for legitimate users inadvertently opens the pool to every user who calls the router, completely defeating the allowlist.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` (line 230–240) calls `_beforeSwap(msg.sender, ...)`. The `sender` argument forwarded to every extension is therefore the immediate caller of `pool.swap()`, not the originating EOA. [1](#0-0) 

**Extension checks that `sender`:**

`SwapAllowlistExtension.beforeSwap` (line 37) evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — i.e., the router address when the swap is router-mediated. [2](#0-1) 

**Router is the pool's `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle` (line 72–80) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. The pool therefore sees `msg.sender == router`, and passes the router address as `sender` to the extension. [3](#0-2) 

**Exploit path:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists a set of trusted EOAs.
2. To let those EOAs use the router, the admin also calls `setAllowedToSwap(pool, router, true)`.
3. Any non-allowlisted EOA calls `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool.
4. The pool calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap proceeds.
5. The non-allowlisted user has bypassed the allowlist entirely.

**No existing guard prevents this:** The extension has no mechanism to inspect `extensionData` for an original caller, and the router provides no such forwarding. The `allowAllSwappers` flag is a separate path and is not the issue here. [4](#0-3) 

## Impact Explanation
The swap allowlist — the sole access-control mechanism for curated pools — is rendered ineffective for any pool that allowlists the router. Any unprivileged user can swap in a pool that was intended to be restricted, violating the pool's access-control invariant. This constitutes broken core pool functionality (allowlist bypass) and falls under the "Admin-boundary break" allowed impact category: an unprivileged path bypasses a required access-control check. Severity: **Medium** (access control broken; no direct principal loss in isolation, but enables unauthorized participation in curated pools which may carry economic consequences depending on pool configuration).

## Likelihood Explanation
The precondition — the pool admin allowlisting the router — is a natural and expected operational step for any curated pool that wants to support router-mediated swaps for its approved users. Once that step is taken, the bypass is trivially reachable by any EOA with no special privileges, no front-running, and no timing dependency. It is fully repeatable.

## Recommendation
The extension must gate on the true originating user, not the immediate caller. Two complementary fixes:

1. **Pass original caller through `extensionData`:** The router should encode `msg.sender` (the EOA) into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when present.
2. **Alternatively, check `tx.origin` as a fallback** (acceptable only if the threat model excludes contract callers) or require the pool admin to never allowlist shared router contracts, documenting this constraint explicitly.

The cleanest fix is option 1: the router encodes the originating user in `extensionData`, and the extension decodes it to perform the allowlist check against the real swapper.

## Proof of Concept
```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // 1. Deploy pool with SwapAllowlistExtension
    // 2. Admin allowlists trustedUser and the router address
    allowlistExt.setAllowedToSwap(pool, address(router), true);
    allowlistExt.setAllowedToSwap(pool, trustedUser, true);
    // attacker is NOT allowlisted
    assertFalse(allowlistExt.allowedSwapper(pool, attacker));

    // 3. Attacker routes through the router — pool sees msg.sender == router
    vm.prank(attacker);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // swap succeeds — allowlist bypassed
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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
