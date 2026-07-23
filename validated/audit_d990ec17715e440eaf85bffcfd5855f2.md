Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as the swapper, not the actual end-user, allowing allowlist bypass or locking out permitted users - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks the router's address rather than the initiating user's address. This breaks the allowlist in two symmetric ways: if the router is allowlisted, every user on the network can bypass the per-pool restriction; if the router is not allowlisted, individually-permitted users cannot swap through the standard periphery at all.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `sender` arriving at the extension is `address(MetricOmmSimpleRouter)`, not the end-user. The same applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). The `beforeSwap` interface provides only `sender` (the direct caller) and `recipient` (the output destination) — neither is reliably the initiating user when routing through an intermediary.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` (the explicit position owner argument), which the pool receives as a separate parameter from the caller and which the router sets to the actual user:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-39
function beforeAddLiquidity(address, address owner, ...)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap interface has no equivalent separate-owner parameter, so there is no analogous field to fall back on.

## Impact Explanation

Two concrete fund-impacting failure modes:

**Mode A — Allowlist fully bypassed (router is allowlisted):** Pool admin allowlists the router so that users can reach the pool through the standard periphery. Because the extension checks the router address, every address on the network can now swap in the pool regardless of individual allowlist status. A pool intended for whitelisted institutional LPs or a private beta becomes open to all, enabling MEV extraction, sandwich attacks, and unauthorized value extraction from the pool's oracle-anchored pricing.

**Mode B — Allowlisted users locked out (router is not allowlisted):** Pool admin allowlists specific user addresses but does not allowlist the router. Every swap attempt through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap` even for permitted users, making the pool's swap flow unusable via the standard periphery. Users must call the pool directly, bypassing slippage protection, deadline checks, and multi-hop routing. This constitutes broken core pool functionality.

Both modes break the core invariant the allowlist is designed to enforce.

## Likelihood Explanation

- `SwapAllowlistExtension` is a production periphery contract, not a mock or test artifact.
- `MetricOmmSimpleRouter` is the primary user-facing swap entry point for `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.
- No special privilege, malicious setup, or non-standard token is required — any user calling any of the four router entry points triggers the wrong-actor check unconditionally.
- The mismatch is structural and repeatable on every router-mediated swap.

## Recommendation

The `beforeSwap` interface does not carry a separate "initiating user" field analogous to `owner` in `beforeAddLiquidity`. Two remediation paths:

1. **Preferred — check `recipient` instead of `sender`:** `recipient` is the address that receives output tokens and is the meaningful economic actor. Change `SwapAllowlistExtension.beforeSwap` to check `recipient` rather than `sender`. This aligns with the deposit extension's pattern of checking the economically relevant party.

2. **Alternative — encode the real user in `extensionData`:** Have the router populate `extensionData` with `msg.sender` before calling the pool, and have the extension decode and verify it. This requires a tamper-evident encoding scheme since `extensionData` is user-supplied.

Additionally, align `SwapAllowlistExtension` with `DepositAllowlistExtension`'s design principle: ignore the intermediary caller and check the economically relevant actor.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in the `beforeSwap` extension order.
2. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — the natural setup so users can swap through the router.
3. Attacker (not individually allowlisted) calls `router.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(params.recipient, ...)` — pool's `msg.sender` is `address(router)`.
5. Pool calls `extension.beforeSwap(address(router), ...)`.
6. Extension checks `allowedSwapper[pool][address(router)]` → `true` → swap proceeds.
7. Attacker successfully swaps in a pool they are not individually permitted to access.

Conversely, if step 2 allowlists `address(user)` (not the router), step 3 reverts for the allowlisted user because `allowedSwapper[pool][address(router)]` is `false`, making the pool unusable through the standard periphery. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
