All three code paths are confirmed in the actual production code. Let me verify the complete chain:

The entire call chain is confirmed by the production code. All claims check out:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` [1](#0-0) 
2. `ExtensionCalling._beforeSwap()` forwards `sender` verbatim into the extension call [2](#0-1) 
3. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` — where `sender` is the router when routing through `MetricOmmSimpleRouter` [3](#0-2) 
4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` with no encoding of the original `msg.sender` into `extensionData` — the user-supplied `params.extensionData` is passed through unmodified [4](#0-3) 

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the end user. If the pool admin allowlists the router — the natural configuration for pools that want to support router-mediated swaps for approved users — the allowlist is completely bypassed: any unprivileged address can swap in a restricted pool simply by calling the public router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards this `sender` verbatim into the ABI-encoded extension call. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point) calls `pool.swap()`, the router is `msg.sender` inside the pool, so `sender = router`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. The router passes `params.extensionData` through unmodified — it never encodes the original caller — so there is no existing mechanism to recover the end user's identity.

**Attack path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` in the `beforeSwap` order to restrict swaps to approved addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that approved users can interact via the router.
3. Attacker (not allowlisted) calls `router.exactInputSingle(...)` targeting the restricted pool.
4. Router calls `pool.swap(...)` — inside the pool, `msg.sender = router`.
5. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker's swap executes despite `allowedSwapper[pool][attacker] == false`.

The pool admin cannot avoid this dilemma: allowlisting the router breaks the allowlist for all users; not allowlisting the router breaks router-mediated swaps for approved users too.

## Impact Explanation
Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. The allowlist — the pool admin's primary mechanism for restricting who may trade — is rendered completely ineffective for router-mediated swaps. Unauthorized swaps in a restricted pool can drain LP liquidity and execute trades the pool admin explicitly prohibited. This breaks the core access-control invariant of the extension framework and matches the **admin-boundary break** and **broken core pool functionality** impact categories.

## Likelihood Explanation
The attack requires only that the pool admin has allowlisted the router, which is the natural and expected configuration for any pool that wants to support router-mediated swaps for its approved users. No privileged access, special tokens, or unusual setup is needed by the attacker — any address can call the public router. The cost is negligible (gas only). The condition is reachable in normal operational deployment.

## Recommendation
The extension must gate the **end user**, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through the router.** Have `MetricOmmSimpleRouter` append `abi.encode(msg.sender)` to `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender` when `sender` is a known router.

2. **Check `recipient` instead of `sender`.** If the pool's design guarantees that `recipient` is always the end user (not the router), the extension can check `recipient`. This must be verified against all router call patterns, including multi-hop paths where intermediate recipients may be the router itself.

The cleanest fix is approach (1): the router appends the end user's address to `extensionData`, and the extension decodes it when `sender` is a recognized router, falling back to `sender` for direct calls.

## Proof of Concept
```solidity
// Preconditions:
// - pool has SwapAllowlistExtension in beforeSwap order
// - pool admin called: extension.setAllowedToSwap(pool, address(router), true)
// - pool admin called: extension.setAllowedToSwap(pool, alice, true)
// - bob is NOT allowlisted

vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);

// Router calls pool.swap() with msg.sender = router
// Extension checks allowedSwapper[pool][router] = true → passes
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(restrictedPool),
    zeroForOne: true,
    amountIn: 1000e18,
    amountOutMinimum: 0,
    recipient: bob,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    tokenIn: address(token0),
    extensionData: ""
}));
// Bob's swap succeeds despite allowedSwapper[pool][bob] == false
// Corrupted invariant: allowedSwapper[pool][bob] == false yet swap executes
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
