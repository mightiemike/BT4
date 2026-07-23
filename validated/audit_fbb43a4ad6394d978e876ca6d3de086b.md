Audit Report

## Title
`SwapAllowlistExtension` Per-User Allowlist Completely Bypassed via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When any user routes through `MetricOmmSimpleRouter`, the router becomes the caller of `pool.swap()`, so the extension checks whether the **router** is allowlisted rather than the actual end-user. If the pool admin allowlists the router (required for any legitimate user to use it), every unprivileged address can bypass the per-user allowlist by routing through the public router contract.

## Finding Description
The call chain is as follows:

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The pool admin faces an impossible choice: allowlist the router (enabling every unprivileged user to bypass the gate) or do not allowlist the router (blocking all legitimately allowlisted users from using the supported periphery path). Either branch breaks the allowlist invariant. [5](#0-4) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional traders, or any other curated set) provides zero on-chain enforcement once the router is allowlisted. Any address can call `MetricOmmSimpleRouter.exactInputSingle` with the pool address and execute a swap that the pool admin explicitly intended to block. This is a complete admin-boundary break: an unprivileged public path bypasses the configured access control, allowing unauthorized users to trade against LP assets under conditions the pool admin did not sanction.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap interface for end-users. Any user who uses the router as intended triggers the bypass. No special privileges, flash loans, or multi-step setup are required — a single public call to any `exact*` function on the router suffices. The bypass is automatic and repeatable by any address.

## Recommendation
The `sender` identity checked by `SwapAllowlistExtension` must be the actual end-user, not the intermediary router. Two sound approaches:

1. **Pass the original user through the router**: Add a `swapper` parameter to `pool.swap()` (separate from `msg.sender`) that the router populates with `msg.sender` before forwarding. The extension then checks that field instead of the pool's `msg.sender`.
2. **Attest the real user via signed payload**: Require the router to encode the real user in `extensionData` with a verifiable attestation. The extension verifies the attestation and checks the attested address against the allowlist.

Until fixed, pool admins must be warned that `SwapAllowlistExtension` provides no protection for swaps routed through `MetricOmmSimpleRouter`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // required for legitimate users
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: attacker,
      ...
  })

  Router calls pool.swap(recipient, ...) with msg.sender = router
  Pool calls _beforeSwap(sender=router, ...)
  Extension checks allowedSwapper[pool][router] → true → passes
  Swap executes for attacker despite attacker not being on the allowlist
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
