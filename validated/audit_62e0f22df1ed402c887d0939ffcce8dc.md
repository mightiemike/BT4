The call chain is fully confirmed by the code. Here is the complete trace:

1. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — `msg.sender` here is the **router** [1](#0-0) 

2. `ExtensionCalling._beforeSwap` encodes and forwards that `sender` (the router) to the extension [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][router]` [3](#0-2) 

4. The router never forwards the originating EOA; it only stores the payer in transient storage for the payment callback, which is never seen by the extension [4](#0-3) 

5. The integration test itself confirms the design: it allowlists `address(callers[0])` (the `TestCaller` wrapper that calls the pool directly), not the underlying EOA `users[0]` [5](#0-4) 

The bypass is real and the claim is technically accurate.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as swapper identity, enabling full per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the end user. If a pool admin allowlists the router to support the standard periphery path, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router, completely defeating the access-control intent of the extension.

## Finding Description
**Root cause:** `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the direct caller of `swap()`. `ExtensionCalling._beforeSwap` encodes this value as `sender` and forwards it to the extension. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When the call originates from `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`, the pool's `msg.sender` is the router. The extension therefore resolves `allowedSwapper[pool][router]`. The router stores the real payer only in transient storage for the payment callback (`_setNextCallbackContext(..., msg.sender, ...)`) and never forwards it to the pool as the swapper identity.

**Exploit flow:**
1. Pool admin deploys pool with `SwapAllowlistExtension`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — allowlists Alice.
3. Admin calls `setAllowedToSwap(pool, router, true)` — allowlists router for periphery support.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient=Bob, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` == `true` → swap executes.
7. Bob bypassed the allowlist in a single transaction with no special privileges.

**Why existing checks fail:** There is no mechanism in the pool or extension to recover the originating EOA. The extension has access only to `sender` (the pool's direct caller) and `extensionData` (caller-supplied bytes). The router does not populate `extensionData` with the user's address.

## Impact Explanation
A pool admin who deploys a curated pool (e.g., KYC-gated, institution-only) using `SwapAllowlistExtension` and also allowlists the router to support the standard periphery path inadvertently opens the pool to all users. The per-user allowlist is completely defeated: any non-allowlisted EOA can call the router and the extension will approve the swap because it sees the allowlisted router address, not the caller's address. This is a broken core pool functionality — the access-control extension does not enforce the policy the pool admin configured — matching the "admin-boundary break" and "broken core pool functionality" impact categories. The pool admin faces an irreconcilable dilemma: allowlist the router and lose per-user control, or block the router and lose the standard periphery path entirely.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is the primary supported periphery swap path; most users will use it.
- Any pool admin who wants both access control and router support will naturally allowlist the router, triggering the bypass.
- No special privileges, malicious setup, or non-standard tokens are required — a standard router call from any EOA is sufficient.
- The bypass is reachable in a single transaction with no preconditions beyond the pool being deployed with `SwapAllowlistExtension` and the router being allowlisted.

## Recommendation
The pool must forward the economically relevant actor — the end user — as `sender` to the extension, not the router's address. Two concrete options:

1. **Router forwards user identity (preferred):** `MetricOmmSimpleRouter` passes the original `msg.sender` (the user) as an explicit `sender` argument to `pool.swap()`, and the pool forwards it to extensions instead of its own `msg.sender`. This preserves the extension interface contract.
2. **Extension reads from `extensionData`:** The router encodes the user's address in `extensionData`; `SwapAllowlistExtension.beforeSwap` decodes and checks it. This is fragile because it depends on the router correctly populating this field for every hop and every allowlisted pool.

Option 1 is the cleaner fix.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // allowlist Alice
3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router for periphery support
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
   → router calls pool.swap(recipient=Bob, ...)
   → pool passes msg.sender=router as `sender` to _beforeSwap
   → extension checks allowedSwapper[pool][router] == true  ✓
   → swap executes — Bob bypassed the allowlist
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist a `TestCaller` wrapper and the router, then call the router from an EOA that is not individually allowlisted and assert the swap succeeds (demonstrating the bypass). Then remove the router from the allowlist and assert the same EOA's router call reverts, confirming the router address is what the extension checks.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
