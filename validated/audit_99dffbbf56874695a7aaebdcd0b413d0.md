Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of `pool.swap()`, so the extension evaluates whether the router is allowlisted — not whether the actual user is allowlisted. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users inadvertently opens the gate to every user, defeating the per-user restriction entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` of that call: [4](#0-3) 

The exploit path is:
1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists `user1` and `address(router)` (to support router-mediated swaps for allowlisted users).
2. `user2` (not allowlisted) calls `router.exactInputSingle()`.
3. Router calls `pool.swap()` → `sender = address(router)` → extension evaluates `allowedSwapper[pool][router]` → `true` → swap succeeds.
4. `user2` receives token output despite not being on the allowlist.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the LP position owner), which is an explicit parameter passed through the call regardless of who calls `addLiquidity()`: [5](#0-4) 

The swap path has no equivalent identity-preserving parameter. The `onlyPool` guard in `BaseMetricExtension` only verifies the caller is a registered pool; it does not verify which user initiated the action: [6](#0-5) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The unauthorized user receives real token output from the pool at oracle-derived prices. This is a direct allowlist bypass causing loss of LP-owned assets to an unauthorized party, matching the "Admin-boundary break" and "allowlist bypass" impact classes in the contest scope.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address — this is a natural, expected operational step. The bypass is therefore reachable on any curated pool that supports router-mediated swaps, which is the common production configuration. No special attacker capability is required beyond calling the public router.

## Recommendation

The extension must gate the actual initiating user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through the router**: Have `MetricOmmSimpleRouter` pass `msg.sender` (the user) as an explicit identity argument, and have the pool forward it — not `msg.sender` — as the `sender` argument to extensions. This requires a core change but preserves identity across the router hop.

2. **Check `recipient` instead of `sender`**: If the pool's `recipient` is always the economic beneficiary of the swap, the extension could check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`. This is a simpler extension-only fix but requires verifying that `recipient` is always the correct identity to gate (and cannot be spoofed by setting `recipient` to an allowlisted address while the actual beneficiary is different).

## Proof of Concept

```solidity
// Pool deployed with SwapAllowlistExtension.
// Pool admin allowlists user1 and the router.
extension.setAllowedToSwap(pool, user1, true);
extension.setAllowedToSwap(pool, address(router), true);

// user2 is NOT allowlisted.
// Direct call: user2 → pool.swap() → sender = user2 → NOT allowlisted → reverts. ✓

// Router call:
// user2 → router.exactInputSingle() → pool.swap() → sender = router → allowlisted → succeeds. ✗
vm.prank(user2);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: user2,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Succeeds — allowlist bypassed. user2 receives token output.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
