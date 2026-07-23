Audit Report

## Title
`SwapAllowlistExtension` checks the direct pool caller (`sender`) instead of the originating user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's `msg.sender` at the time `swap()` is called. When `MetricOmmSimpleRouter` mediates the swap, `sender` resolves to the router address, not the originating EOA. Because the router must be allowlisted for any router-mediated swap to succeed, every user — including those explicitly excluded — can bypass the gate by routing through the public router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap()` populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← sender forwarded to extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` passes this `sender` directly into the ABI-encoded call to the extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with the router as `msg.sender`. [5](#0-4) 

The broken invariant: the pool admin cannot simultaneously (a) allow allowlisted users to swap through the router and (b) block non-allowlisted users from doing the same. Once the router address is added to the allowlist — required for any router-mediated swap to work — every user can call `MetricOmmSimpleRouter.exactInputSingle()` and the extension will pass them through, because the check resolves to the router (allowlisted), not the caller (not allowlisted). The `extensionData` field passed by the router is an opaque caller-controlled bytes field with no integrity guarantee, so it cannot be used as a trusted carrier of the originating user without additional verification. [6](#0-5) 

## Impact Explanation
Direct loss of access-control invariant for permissioned pools. Pools that deploy `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd users, specific market makers, or whitelisted integrators) have their gate rendered ineffective the moment the router is allowlisted. Any unprivileged user can execute swaps against the pool, accessing oracle-derived prices that were only intended to be available to approved parties. This constitutes broken core pool functionality with direct fund-impacting consequences for LPs who deposited under the assumption that the allowlist was enforced. [7](#0-6) 

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard, publicly deployed entry point for swaps. Any user who inspects the contract can discover that routing through it substitutes the router address for their own in the extension check. No privileged access, special tokens, or unusual setup is required — a single call to `exactInputSingle` suffices. The pool admin enabling router-mediated swaps for legitimate users is a normal operational step that simultaneously opens the bypass. [6](#0-5) 

## Recommendation
`SwapAllowlistExtension` must check the originating user, not the direct pool caller. Two viable approaches:

1. **Pass the original user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and have the extension decode and verify it. The extension must also verify that the claim comes from a trusted router (e.g., via a factory-registered router registry) to prevent spoofing via a malicious `extensionData` payload.

2. **Restrict to direct pool interaction only**: Document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps. The extension should revert if `sender` is a known router address, forcing direct pool interaction only. [1](#0-0) 

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1).
  - Pool admin calls setAllowedToSwap(pool, allowedUser, true).
  - Pool admin calls setAllowedToSwap(pool, router, true)
      (required so allowedUser can use the router).

Attack:
  - attacker (not in allowlist) calls:
      MetricOmmSimpleRouter.exactInputSingle({
          pool: pool,
          recipient: attacker,
          zeroForOne: true,
          amountIn: X,
          ...
      })
  - Router calls pool.swap(attacker, true, X, ...) with router as msg.sender.
  - Pool calls _beforeSwap(msg.sender=router, ...).
  - Extension evaluates allowedSwapper[pool][router] → true → passes.
  - Swap executes; attacker receives output tokens.

Expected: revert NotAllowedToSwap().
Actual:   swap succeeds; allowlist bypassed.
``` [8](#0-7) [2](#0-1) [9](#0-8)

### Citations

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
