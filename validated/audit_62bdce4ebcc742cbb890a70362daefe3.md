Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit any user to swap through it), the allowlist becomes unconditionally open to every address that calls the router, defeating the access-control guard entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool. Critically, the router does not embed the original `msg.sender` into `extensionData`; it passes `params.extensionData` verbatim from the caller, and the extension ignores `extensionData` entirely: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly with the router as `msg.sender`.

The result is a two-state trap: if the router is not allowlisted, no user can swap through it (even allowlisted ones); if the router is allowlisted, every user can swap through it regardless of their own allowlist status. There is no configuration that permits only allowlisted users to swap via the router.

## Impact Explanation
The swap allowlist is the protocol's primary access-control mechanism for restricted pools. Its complete bypass means any unprivileged address can execute swaps in a pool configured to be permissioned. Protocol-level invariants that depend on the allowlist (regulatory compliance, LP-agreed counterparty restrictions, circuit-breaker whitelists) are silently violated. LPs who deposited under the assumption that only vetted counterparties could trade against their liquidity are exposed to unrestricted adverse selection. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" gate.

## Likelihood Explanation
The trigger is a natural, expected configuration: any pool that deploys `SwapAllowlistExtension` and also wants users to interact through the standard `MetricOmmSimpleRouter` must allowlist the router. The bypass requires no privileged access, no special token behavior, and no malicious setup — only a call to a public router function. Any user who reads the `allowedSwapper` mapping and sees the router address present can immediately exploit it.

## Recommendation
The extension must gate on the economic actor (the address that controls the input tokens and benefits from the output), not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the original `msg.sender` through the router via `extensionData`.** The router should encode the original caller into `extensionData` before calling `pool.swap()`, and the extension should decode and verify it. The `beforeSwap` hook already receives `extensionData` as its last parameter.

2. **Check `recipient` instead of `sender` in the extension.** For exact-input swaps the recipient is the economic beneficiary. This is imperfect for multi-hop flows but eliminates the router-identity problem for single-hop cases.

Either way, the extension's allowlist key must be the address the pool admin intended to gate, not the intermediary contract.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router users
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  attacker (not on allowlist) calls:
    router.exactInputSingle({
      pool: restrictedPool,
      tokenIn: token0,
      tokenOut: token1,
      recipient: attacker,
      ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=attacker, ..., extensionData="")   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, attacker receives token1

Result:
  attacker bypasses the allowlist and executes a swap in a restricted pool.
  The extension never sees the attacker's address.
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
