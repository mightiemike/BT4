Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender` — the router's address when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to permit router-mediated swaps, every address on the network can bypass the per-user swap allowlist by calling the router. Conversely, if the admin does not allowlist the router, individually allowlisted users cannot use the router at all.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to the extension hook:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router `msg.sender` to the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  ...
);
```

The extension has no visibility into who called the router. The check passes for any caller as long as the router address is in `allowedSwapper[pool]`.

**Existing guards are insufficient:** The only guard is `allowedSwapper[pool][sender]`, and `sender` is structurally the router when the router is used. There is no fallback check on `recipient` or any mechanism to propagate the original caller's identity.

## Impact Explanation
**Allowlist bypass (High):** A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise curated addresses. To allow those users to use the primary user-facing entry point (`MetricOmmSimpleRouter`), the admin must allowlist the router address. Once the router is allowlisted, any unprivileged address can call `router.exactInputSingle(...)` and the extension check passes because `sender = router` is allowlisted. The per-user allowlist is completely nullified — the pool is open to all swappers. This is a broken core invariant: the allowlist fails to gate the intended actors, directly enabling unauthorized access to a restricted pool.

**Broken core functionality (Medium):** If the admin allowlists individual user addresses but does not allowlist the router, those allowlisted users cannot use the router (the router's address fails the check). They must implement `IMetricOmmSwapCallback` themselves to call the pool directly, rendering the primary swap path unusable for any allowlist-gated pool.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool that deploys `SwapAllowlistExtension` and expects users to swap via the router will immediately encounter this issue. The operator pattern (payer ≠ beneficiary, `msg.sender` ≠ `owner`) is explicitly documented for `addLiquidity` in the interface NatSpec, but the analogous separation for swaps (caller ≠ recipient, router ≠ end user) was not accounted for in `SwapAllowlistExtension`. No special attacker capability is required — any address can call the router.

## Recommendation
Gate on `recipient` instead of `sender`. The `recipient` is the economic beneficiary of the swap and is always the end user's address, even through the router:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, require the router to encode the original caller in `extensionData` and decode it in the extension, though this is more complex and requires router cooperation.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted.
vm.prank(alice);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        recipient: alice,
        tokenIn: address(token0),
        deadline: block.timestamp,
        extensionData: ""
    })
);
// ✓ swap succeeds — alice bypassed the per-user allowlist
// beforeSwap receives sender = address(router), which passes allowedSwapper[pool][router]
```

The call chain is: `alice → router.exactInputSingle() → pool.swap(msg.sender=router) → _beforeSwap(sender=router) → SwapAllowlistExtension.beforeSwap(sender=router)` — the check passes because the router is allowlisted, regardless of who called the router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
