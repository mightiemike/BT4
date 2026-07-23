Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing any caller to bypass per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` validates the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap(...)` call. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. A pool admin who allowlists the router address (the only way to enable router-based swaps for allowlisted users) inadvertently grants every caller — including non-allowlisted addresses — the ability to bypass the per-user gate by routing through the router.

## Finding Description
**Root cause — pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension, so `sender` in the extension is always the immediate caller of `pool.swap(...)`, not the originating EOA.

**Extension checks only `sender` (the immediate pool caller):**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [2](#0-1) 

**Router calls `pool.swap(...)` directly, making itself `msg.sender`:**

For `exactInputSingle`, the router calls `pool.swap(...)` with no forwarding of the originating user: [3](#0-2) 

For multi-hop `exactInput`, every hop follows the same pattern: [4](#0-3) 

**Exploit path:**
A pool admin who wants allowlisted users to swap through the router must call `setAllowedToSwap(pool, router, true)`. Once `allowedSwapper[pool][router] == true`, the check `allowedSwapper[pool][router]` passes for every caller regardless of who the originating EOA is. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle(...)` and the extension passes because `sender == router` is allowlisted. [5](#0-4) 

There is no existing guard in the extension, the router, or the pool that forwards or verifies the originating user's identity.

## Impact Explanation
`SwapAllowlistExtension` is the sole mechanism for curated pools to restrict who may trade. Once the router is allowlisted (the only way to support router-based swaps), the allowlist provides no protection: any address can swap by routing through `MetricOmmSimpleRouter`. Non-allowlisted users gain full swap access to pools designed to be restricted, directly violating the pool's access-control invariant and potentially exposing LP funds to actors the pool admin explicitly excluded. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path (routing through the public router) bypasses a configured access control.

## Likelihood Explanation
The router is the primary user-facing swap interface deployed alongside the core pool. Pool admins who configure a `SwapAllowlistExtension` and also want their allowlisted users to access the router will naturally allowlist the router address via `setAllowedToSwap`. There is no warning in the extension, the interface, or the documentation that doing so voids per-user gating. The trigger is a routine, well-motivated admin action, and the bypass is repeatable by any unprivileged address.

## Recommendation
The extension must verify the originating user, not the immediate pool caller. Two viable approaches:

1. **Router-forwarded identity in `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it against a trusted router registry stored in the extension.
2. **Trusted intermediary registry**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted intermediary, require the originating user's address to be supplied and verified via `extensionData`.

The simplest safe default is to provide a router variant that encodes the originating user's address in `extensionData` and update `SwapAllowlistExtension.beforeSwap` to decode and check that address when `sender` is a known trusted router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended user
  bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)              // MetricOmmSimpleRouter.sol L72-80
    → pool calls _beforeSwap(msg.sender=router, ...)      // MetricOmmPool.sol L230-240
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓             // SwapAllowlistExtension.sol L37
    → swap executes for bob despite bob not being allowlisted
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
