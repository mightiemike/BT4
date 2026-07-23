Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool binds to its own `msg.sender` — the router — when a user swaps via `MetricOmmSimpleRouter`. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user regardless of allowlist status can bypass the per-user gate by routing through the router. This renders the allowlist ineffective on the protocol's primary swap entrypoint.

## Finding Description

**Root cause:** `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231, passing the immediate caller as `sender`. [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` directly to the extension via `abi.encodeCall`. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the pool's `msg.sender` the router address — not the end user. [4](#0-3) 

**Exploit path:**
```
bannedUser → MetricOmmSimpleRouter.exactInputSingle(...)
  → pool.swap(...) [msg.sender = router]
    → _beforeSwap(sender = router, ...)
      → SwapAllowlistExtension.beforeSwap
        → allowedSwapper[pool][router] == true  ← passes, bannedUser never checked
```

**Irresolvable admin dilemma:**
- Router NOT allowlisted → allowlisted users cannot swap via the router (broken UX on the primary periphery path).
- Router IS allowlisted → every user bypasses the allowlist through the router.

No existing guard in the extension, pool, or router prevents this. The `extensionData` field is passed through but the extension never reads it to recover the original caller. [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` for access control (KYC-only, institutional-only, or counterparty-restricted pools) is fully bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The non-allowlisted user executes swaps against the pool's liquidity, violating the pool's curation policy and potentially draining LP value. This constitutes broken core pool functionality (the allowlist guard fails open on the supported public swap path) and direct loss of LP assets. [6](#0-5) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Pool admins configuring a swap allowlist will need to support router-based swaps for their allowlisted users, so they will allowlist the router. Once the router is allowlisted, the bypass is trivially reachable by any user with no special privileges, no malicious setup, and no non-standard tokens. The bypass is repeatable on every swap. [7](#0-6) 

## Recommendation

The extension must gate the economically responsible actor, not the intermediary. Two approaches:

1. **Router encodes real caller in `extensionData`**: Have `MetricOmmSimpleRouter` ABI-encode `msg.sender` into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a protocol-level convention for the encoding.
2. **Transient storage**: The router already writes payer context to transient storage via `_setNextCallbackContext`. A standardized transient slot for the original caller could be written by the router and read by the extension, so the allowlist always gates the real user regardless of periphery path. [8](#0-7) 

## Proof of Concept

```solidity
// Setup: pool admin allowlists only allowedUser and the router
swapAllowlist.setAllowedToSwap(address(pool), allowedUser, true);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// bannedUser (NOT in allowlist) calls through the router
vm.prank(bannedUser);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: bannedUser,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Swap succeeds: extension checked allowedSwapper[pool][router] = true,
// bannedUser was never checked.
```

The existing test `test_allowedSwapSucceeds` in `FullMetricExtension.t.sol` allowlists `callers[0]` (a `TestCaller` contract) directly — it does not test the router-mediated path, confirming the bypass is untested. [9](#0-8)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
