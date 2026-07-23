Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user address, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the pool's own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end-user. The allowlist therefore checks whether the **router** is approved, not whether the **user** is approved. This produces two broken outcomes: (A) if the router is allowlisted, any user bypasses the gate; (B) if the router is not allowlisted, all router-mediated swaps revert for every user, including individually allowlisted ones.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

This value becomes the `sender` parameter in `SwapAllowlistExtension.beforeSwap`: [2](#0-1) 

The enforcement check is `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][pool's msg.sender]`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [3](#0-2) 

The call chain is: `user → router.exactInputSingle() → pool.swap() → extension.beforeSwap(sender = address(router))`. The allowlist lookup resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The `onlyPool` modifier in `BaseMetricExtension` only validates that the caller is a registered pool — it does not validate the identity of the economic actor: [5](#0-4) 

The allowlist storage and setter are correctly structured by `(pool, swapper)`: [6](#0-5) [7](#0-6) 

But enforcement consumes the intermediary router address, not the intended swapper address, breaking the invariant the admin intended to enforce.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set (e.g., KYC'd users, whitelisted institutions) is rendered ineffective for all router-mediated swaps, which is the primary supported public entrypoint. **Outcome A:** If the pool admin adds the router to the allowlist to enable router-based trading, every user — including those explicitly excluded — can bypass the gate by routing through `MetricOmmSimpleRouter`. LP funds are exposed to swappers the pool admin explicitly excluded. **Outcome B:** If the router is not allowlisted, all router-mediated swaps revert for every user, including individually allowlisted ones, making the supported periphery path entirely unusable on allowlisted pools. Both outcomes constitute broken core pool functionality and a direct admin-boundary break.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint. Any user who discovers the allowlist can trivially route around it using the standard router without any special setup, privileged access, or custom contracts. The trigger is a single public transaction through a supported, documented contract. No preconditions beyond the pool admin having configured the allowlist are required.

## Recommendation

The pool must forward the originating end-user's address through the call chain so the extension can check it. Two approaches:

1. **Encode the true caller in `extensionData`:** Require `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` before calling `pool.swap()`. Have `SwapAllowlistExtension.beforeSwap` decode and check that address. The pool should verify the router is a trusted forwarder before accepting the encoded address.

2. **Add an explicit `originSender` parameter to `pool.swap()`:** Have the pool accept and forward an explicit originating-user address to extension hooks. `MetricOmmSimpleRouter` would populate this with its own `msg.sender`.

The invariant to enforce: the address checked against `allowedSwapper` must be the address that economically initiates the swap (the end-user), not any intermediate contract in the call chain.

## Proof of Concept

```solidity
// Pool admin sets up an allowlist: only `allowedUser` may swap.
swapAllowlist.setAllowedToSwap(address(pool), allowedUser, true);

// Attacker calls pool.swap() directly → reverts correctly (sender = attacker, not allowlisted).
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, false, 1000, type(uint128).max, "", "");

// Pool admin adds the router so that allowedUser can trade via the router.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Attacker routes through MetricOmmSimpleRouter → passes the allowlist check
// because extension sees sender = address(router), which IS allowlisted.
vm.prank(attacker);
// Succeeds — attacker bypasses the allowlist entirely.
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token1,
    extensionData: ""
}));
// Attacker received swap output from a pool they were explicitly excluded from.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
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
