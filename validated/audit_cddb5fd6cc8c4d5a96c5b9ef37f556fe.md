Audit Report

## Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Allowlist Identity Check — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `MetricOmmPool::swap`. When a user routes through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender`, so `sender` is the router's address rather than the originating user. The allowlist check therefore operates on the wrong identity, producing two concrete failure modes: allowlist bypass (Mode A) and allowlisted users blocked from using the router (Mode B).

## Finding Description
`MetricOmmPool::swap` unconditionally passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`_beforeSwap` forwards that value as `sender` to the extension via `_callExtensionsInOrder`. The extension then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the router — not the end user.

In `MetricOmmSimpleRouter::exactInputSingle` (and `exactOutputSingle`, `exactInput`, `exactOutput`), the router calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The original `msg.sender` (the user) is stored only in transient callback context for payment purposes and is never forwarded to the pool's `swap` call. The router becomes the pool's `msg.sender`, so `sender = router` in every router-mediated swap.

**Mode A — Allowlist bypass:** A pool admin allowlists the router to enable router-mediated swaps. Because `sender = router` for every router-mediated swap, any unprivileged user can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The check `allowedSwapper[pool][router] == true` passes for all users.

**Mode B — Allowlisted users blocked:** A pool admin allowlists specific user addresses but not the router. Those users' router-mediated swaps revert because `allowedSwapper[pool][router] == false`, making the canonical router unusable for any allowlist-gated pool.

Existing guards are insufficient: the `whenNotPaused` modifier reverts before `_beforeSwap` only when `pauseLevel != 0`; it does not affect the identity mismatch when the pool is active.

## Impact Explanation
`SwapAllowlistExtension` is the sole mechanism for restricting swap access on a pool. Its failure means either unauthorized parties execute swaps on a restricted pool (Mode A — potential LP value drain or execution at prices the admin did not intend to permit), or legitimate allowlisted users cannot use the canonical router (Mode B — broken core swap functionality). Mode A in particular can lead to direct value leakage if the pool is configured to allow only trusted counterparties such as market makers with agreed-upon price ranges. This constitutes broken core pool functionality causing loss of funds or unusable swap flows.

## Likelihood Explanation
Any pool deploying `SwapAllowlistExtension` and expecting router-mediated swaps to respect the allowlist is affected. The router is the primary public entrypoint for end users. The misconfiguration is non-obvious: a pool admin who allowlists the router to "enable router swaps" unknowingly opens the pool to all users. No special attacker capability is required beyond calling the public router functions.

## Recommendation
Pass the originating user's address through the call chain. One approach: have the router encode the original `msg.sender` in `extensionData` and have the extension decode it from there. A cleaner protocol-level fix is to add an `originator` field to the swap call that the pool passes to hooks alongside `sender`, allowing extensions to distinguish the immediate caller from the end user.

## Proof of Concept
```solidity
// Pool configured with SwapAllowlistExtension; only `trustedUser` is allowlisted.
// Router is NOT allowlisted.

// Step 1: trustedUser tries to swap via router → REVERTS (Mode B)
vm.prank(trustedUser);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// SwapAllowlistExtension checks allowedSwapper[pool][router] == false → NotAllowedToSwap

// Step 2: Pool admin allowlists the router to fix Mode B
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Step 3: Unprivileged attacker bypasses the allowlist (Mode A)
vm.prank(attacker); // attacker is NOT in the allowlist
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → PASSES
// Attacker executes swap on a supposedly restricted pool.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
