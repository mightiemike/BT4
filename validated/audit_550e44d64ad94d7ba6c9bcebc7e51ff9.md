Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against a per-pool allowlist, but `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender`. When `MetricOmmSimpleRouter` is the entry point, `msg.sender` inside the pool is the router contract, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router to permit allowlisted users to trade through the standard periphery simultaneously opens the pool to every non-allowlisted address on the network.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without encoding the originating EOA anywhere in the call: [4](#0-3) 

At that point `msg.sender` inside the pool is the router contract address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router stores `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext`), and never encodes it into `extensionData` for the extension to read. There is no mechanism in the router or the extension protocol to thread the original EOA through to the hook. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

## Impact Explanation
A curated pool (KYC-only, institution-only, or protocol-internal) that deploys `SwapAllowlistExtension` and allowlists the router loses all access control over who can trade. Any non-allowlisted address can execute swaps by routing through `MetricOmmSimpleRouter`, draining LP value at oracle-derived prices. This constitutes a direct loss of LP principal and broken core pool functionality: the pool's stated access policy is silently voided for every router-mediated swap. This matches the allowed impact criteria of "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path."

## Likelihood Explanation
The router is the canonical, documented entry point for end-users. A pool admin who wants allowlisted users to be able to use the standard UI/SDK will inevitably allowlist the router. The bypass requires no special privilege, no flash loan, and no multi-step setup — a single `exactInputSingle` call from any EOA suffices. The precondition (router allowlisted) is the natural and expected operational state for any curated pool that supports the standard periphery.

## Recommendation
The extension must identify the true economic actor, not the intermediary contract. Two viable approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and verifies it. This requires a convention between router and extension but keeps the core unchanged.

2. **Trusted forwarder registry**: Add a `trustedForwarder` registry to the extension so that when `sender == router`, the extension reads the actual user from a router-supplied field in `extensionData`.

Either way, the extension must never treat a shared intermediary contract as the identity to gate.

## Proof of Concept
```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as extension1.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that allowlisted users can reach the pool via the router.
3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack
──────
4. Attacker (non-allowlisted EOA) calls:
       router.exactInputSingle({pool: pool, ..., extensionData: ""})

5. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", "").
   pool.msg.sender = router.

6. Pool calls _beforeSwap(sender=router, ...).

7. Extension evaluates:
       allowAllSwappers[pool]          → false
       allowedSwapper[pool][router]    → true   ← set in step 2

8. Extension returns selector — no revert.

9. Swap executes at oracle price. Attacker receives output tokens.
   Allowlist is completely bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
