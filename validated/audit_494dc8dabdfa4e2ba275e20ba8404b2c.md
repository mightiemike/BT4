Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If a pool admin allowlists the router to enable router-mediated swaps, every user who calls the router is implicitly allowlisted, completely defeating the curation purpose of the extension.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool passed — the router address when the user entered through the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly without injecting the original user's address anywhere the extension can read it — `msg.sender` is stored only in the internal callback context, not forwarded to the pool's `sender` parameter: [4](#0-3) 

The exact wrong value is `allowedSwapper[pool][router]` being evaluated instead of `allowedSwapper[pool][user]`. No existing guard corrects this: the extension has no access to the original caller, and the pool has no mechanism to forward it.

## Impact Explanation
Two fund-impacting outcomes follow directly:

**Scenario A — Allowlist bypass (Critical):** A pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists the router so that allowlisted users can swap through the standard periphery. Because the extension checks the router address, every user who calls the router is now implicitly allowlisted. The curation boundary is completely erased; any address can trade against the pool's liquidity, exposing LPs to unrestricted counterparties they explicitly excluded.

**Scenario B — Broken core swap functionality (High):** A pool admin allowlists specific EOAs but does not allowlist the router. Those EOAs cannot swap through the router at all — the extension reverts with `NotAllowedToSwap` because the router is not in the allowlist. The primary user-facing swap path is broken for every legitimately allowlisted user, rendering the pool effectively unusable through the supported periphery.

Both outcomes directly affect LP assets and constitute broken core pool functionality.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool deploying `SwapAllowlistExtension` and expecting users to interact through the router will immediately encounter one of the two failure modes. No special attacker capability is required — a normal user calling `exactInputSingle` or `exactInput` is sufficient to trigger the bypass or the revert. The condition is met by default for any router-integrated allowlisted pool.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary contract. Recommended approaches:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between the router and the extension.
2. **Standardized identity forwarding:** Add an `originalSender` field to the swap call or callback data that the pool forwards to extensions, so extensions can always recover the true initiator regardless of intermediary.
3. **Key on `recipient` instead of `sender`:** Redesign the allowlist to check the `recipient` (the address receiving output tokens), since the recipient is always the user-controlled address even through the router.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
swapAllowlist.setAllowedToSwap(pool, alice, true);
// Pool admin also allowlists the router so alice can use it:
swapAllowlist.setAllowedToSwap(pool, address(router), true);

// Attack: bob (not allowlisted) swaps through the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Succeeds — extension checked allowedSwapper[pool][router] == true
// Expected revert — bob is not allowlisted

// Alternatively, without allowlisting the router:
// alice (allowlisted) tries to use the router → reverts NotAllowedToSwap
// because extension checks allowedSwapper[pool][router] == false
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
