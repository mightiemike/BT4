Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any unprivileged address to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is set to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. If the pool admin allowlists the router address to enable router-mediated swaps, every non-allowlisted user can bypass the per-user gate by calling the router. The per-user allowlist invariant is completely broken.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap()
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making itself `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

**Two broken scenarios:**

1. **Allowlist bypass (critical path):** Admin allowlists the router address so users can swap via the router. Because the check is on `sender` = router, every user — including non-allowlisted ones — passes the gate when routing through `MetricOmmSimpleRouter`. The per-user allowlist is rendered completely ineffective.

2. **Allowlisted users locked out of the router:** Admin allowlists individual EOA addresses. Those users cannot swap through the router because `sender` = router address, which is not on the allowlist. They are forced to call the pool directly, losing slippage protection, multi-hop routing, and deadline enforcement.

**Existing guards are insufficient:** The `allowAllSwappers` flag is a separate escape hatch that bypasses the per-user check entirely. There is no mechanism in the extension or the router to attest the real end-user identity.

## Impact Explanation
A pool deployed with `SwapAllowlistExtension` (e.g., a KYC-gated or permissioned pool) is fully open to any caller who routes through `MetricOmmSimpleRouter` if the router is allowlisted. Non-allowlisted users can execute swaps, draining LP value at oracle prices. This is a direct loss of the pool's access-control invariant and constitutes broken core pool functionality with fund-impacting consequences for LPs who deposited under the assumption that only allowlisted counterparties could trade.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who enables the router for their allowlisted pool (the natural operational setup) triggers the bypass. The attacker needs no special privilege — a single call to `exactInputSingle` with any non-zero amount suffices. The condition is trivially reachable by any EOA.

## Recommendation
The extension must check the end user, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and have the extension read the attested user address from `extensionData` when present. Specifically:

- `MetricOmmSimpleRouter` encodes `msg.sender` as the first word of `extensionData` before calling `pool.swap`.
- `SwapAllowlistExtension.beforeSwap` decodes the real user from `extensionData` (if non-empty) and checks `allowedSwapper[pool][realUser]` instead of `allowedSwapper[pool][sender]`.
- If `extensionData` is empty (direct pool call), fall back to checking `sender` directly.

This is the only approach that survives router indirection without requiring the pool core to be modified.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, address(router), true)
    // admin intends to allow router-mediated swaps for allowlisted users

Attack:
  attacker = non-allowlisted EOA
  attacker calls router.exactInputSingle({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: X,
    extensionData: ""
  })

  // router calls pool.swap(recipient=attacker, ...)
  // pool calls _beforeSwap(sender=router, ...)
  // extension checks allowedSwapper[pool][router] → true
  // swap executes — attacker bypassed the per-user allowlist
```

The `sender` the extension sees is always the router address, never the attacker's address. The allowlist check passes unconditionally for any user routing through the allowlisted router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
