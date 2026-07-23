All four code paths cited in the claim are confirmed against the actual repository. The vulnerability is valid.

Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via Router When Router Address Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When `MetricOmmSimpleRouter` mediates a swap, the router is `msg.sender` at the pool, so the extension checks the router address rather than the end user. Any pool admin who allowlists the router to permit approved users to trade via the standard periphery simultaneously grants unrestricted swap access to every caller of the router, including addresses that are explicitly not allowlisted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the forwarded caller: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same applies to multi-hop `exactInput`, where intermediate hops also originate from the router: [5](#0-4) 

Root cause: the extension has no mechanism to distinguish the router acting as an intermediary from the router acting as the true end user. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][router]` returns `true` for every caller of the router regardless of their individual allowlist status. The allowlist degenerates to a binary router-on/router-off switch.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a curated set of counterparties. Once the router is allowlisted, any address can call `exactInputSingle`, `exactInput`, or `exactOutputSingle` and execute swaps at oracle-derived bid/ask prices. Unauthorized swappers extract value from LP bins at oracle mid prices, causing direct loss of LP principal. This is a broken core access-control invariant with direct loss of user funds, meeting the Critical/High threshold under the allowed impact gate. [6](#0-5) 

## Likelihood Explanation
The trigger is a routine, non-malicious admin action: adding the router to the allowlist so that approved users can interact via the standard periphery. No privileged escalation, oracle manipulation, or non-standard token is required. `MetricOmmSimpleRouter` is a public, permissionless contract, so any user who discovers the router is allowlisted can immediately exploit it. The condition is expected to exist in every production pool that uses both `SwapAllowlistExtension` and the router. [7](#0-6) 

## Recommendation
The extension must verify the end user, not the immediate caller. Two sound approaches:

1. **Pass the original user through the router.** Add a `swapper` field to the extension data that the router populates with `msg.sender` before calling the pool. The extension reads and verifies this field instead of (or in addition to) `sender`.

2. **Router-aware allowlist entry.** If `sender` equals a known router address, require the router to attest the real user via signed extension data or a dedicated router-aware allowlist entry. This keeps the extension stateless while preserving per-user granularity.

Either way, the extension must not treat the router address as a proxy for the end user's identity. [3](#0-2) 

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is approved)
  allowedSwapper[pool][router] = true   (admin adds router so alice can use it)
  allowedSwapper[pool][bob]    = false  (bob is NOT approved)

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap receives sender=router
    → checks allowedSwapper[pool][router] == true  ✓
    → swap proceeds; bob extracts tokens from LP bins at oracle price

Result:
  bob, who is explicitly not allowlisted, completes a swap in a restricted pool.
  LP funds are transferred to bob at oracle-derived prices.
  alice's individual allowlist entry is irrelevant; the router entry grants access to all.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][router] = true` and `allowedSwapper[pool][bob] = false`, call `exactInputSingle` from `bob`, assert the swap succeeds and `bob` receives output tokens. [4](#0-3)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
