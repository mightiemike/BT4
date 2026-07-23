Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Any Unprivileged Caller to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router contract address, not the originating EOA. If the pool admin allowlists the router to permit allowlisted users to access the pool via the router, every unprivileged user can bypass the allowlist by routing through the same public contract.

## Finding Description

`MetricOmmPool.swap` is a plain `external` function that passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — i.e., `allowedSwapper[pool][directCaller]`:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [4](#0-3) 

The same applies to `exactInput` (all hops), `exactOutputSingle`, `exactOutput`, and recursive hops inside `_exactOutputIterateCallback`. [5](#0-4) 

A pool admin who wants allowlisted users to access the pool via the router **must** add the router to `allowedSwapper[pool]`. Once `allowedSwapper[pool][router] = true`, the extension check passes for **every** caller of the router, regardless of whether that caller is on the allowlist. There is no mechanism in the router to encode or verify the originating EOA's allowlist status.

## Impact Explanation

A pool protected by `SwapAllowlistExtension` is intended to restrict swaps to a curated set of counterparties (e.g., KYC-verified traders, institutional partners). Once the router is allowlisted, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool. The pool's `beforeSwap` hook sees `sender = router` → allowlist check passes → the non-allowlisted user executes a swap at the oracle-anchored bid/ask price. This constitutes a broken access-control invariant with direct fund-impacting consequences: LPs deposited under the assumption that only vetted counterparties could trade, and non-allowlisted users can extract value from them if the oracle price is even momentarily favorable.

**Severity: High** — the bypass is unconditional once the router is allowlisted; no special privilege or token is required.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract with no access controls on its entry points.
- Pool admins who deploy a swap-allowlisted pool and also want allowlisted users to use the router **must** add the router to the allowlist; there is no other supported path.
- Any attacker who observes `allowedSwapper[pool][router] = true` on-chain can immediately exploit the bypass.
- No admin action, flash loan, or special setup is required beyond a standard `exactInputSingle` call.

**Likelihood: High.**

## Recommendation

Pass the originating user's address through the extension data or add a dedicated `originator` field to the `beforeSwap` hook signature. A minimal fix is to have the router encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router. A more robust fix is to add an `originator` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates from a verified transient-storage slot set by the router before calling `swap`.

## Proof of Concept

```
Setup
─────
1. Deploy a pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin adds Alice (allowlisted user) and the router to allowedSwapper[pool].
3. Bob (non-allowlisted) is NOT in allowedSwapper[pool].

Attack
──────
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=Bob, ...).
   → msg.sender seen by pool = router address.
6. Pool calls _beforeSwap(sender=router, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
8. Bob's swap executes at oracle price; Bob is never checked against the allowlist.

Result
──────
Bob, an unprivileged non-allowlisted user, successfully swaps in a pool
that was configured to restrict access to vetted counterparties only.
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
