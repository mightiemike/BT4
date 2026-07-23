Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter`: Any Unprivileged User Can Swap in Gated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool populates with its own `msg.sender`. When `MetricOmmSimpleRouter` is used, the router becomes the pool's `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If the pool admin allowlists the router to permit any allowlisted user to trade through it, the gate is fully open to every address on-chain.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```
where `msg.sender` is the pool and `sender` is the value forwarded by the pool from its own `msg.sender` at `MetricOmmPool.swap` L230–231:
```solidity
_beforeSwap(msg.sender, recipient, ...)
```
`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly at L72–80, making the router the pool's `msg.sender`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. There is no mechanism in the router to forward the real caller's identity, and `extensionData` is passed through as-is from the caller (`params.extensionData`), so a malicious user can supply arbitrary data. The existing guard — checking `sender` — is structurally insufficient for any router-mediated path.

## Impact Explanation
The `SwapAllowlistExtension` is the sole on-chain mechanism for pools that restrict trading to specific counterparties (KYC'd users, institutional LPs, whitelisted market makers). Once the router is allowlisted — the only way to let any allowlisted user trade through it — every address on-chain can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity without restriction. LP funds are directly exposed to counterparties the pool admin explicitly intended to exclude. The pool's core access-control invariant is fully broken for all router-mediated paths. This constitutes a broken core pool functionality causing direct exposure of LP assets to unauthorized counterparties, meeting the High severity threshold.

## Likelihood Explanation
The router is a public, permissionless contract. No special privilege, timing window, or negligence is required beyond discovering that the pool uses `SwapAllowlistExtension`. Any user can immediately route through `MetricOmmSimpleRouter` to bypass the gate. The bypass is structural and repeatable on every block.

## Recommendation
The extension must gate the originating user, not the intermediary contract. The preferred fix is to have the router encode `msg.sender` into a standardized field of `extensionData` and have the extension decode and verify it — falling back to `sender` only when `sender` is not a known router. Alternatively, the router can be made to pass the real caller's address in a trusted, authenticated manner (e.g., a signed payload). At minimum, documentation must clearly state that `SwapAllowlistExtension` only gates direct `pool.swap()` calls and is ineffective for router-mediated swaps, so pool admins do not deploy it under the false assumption that it covers all swap paths.

## Proof of Concept
```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)
  - Pool admin calls E.setAllowedToSwap(P, router, true)  ← required for alice to use the router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ...) — router is msg.sender (MetricOmmSimpleRouter.sol L72-80)
  3. Pool calls _beforeSwap(sender=router, ...) (MetricOmmPool.sol L230-231)
  4. Extension checks allowedSwapper[P][router] → true (SwapAllowlistExtension.sol L37)
  5. Swap proceeds; bob receives output tokens from the pool

Result:
  - bob, an explicitly non-allowlisted address, successfully swaps against the restricted pool
  - The allowlist guard is fully bypassed
  - LP funds are exposed to an unauthorized counterparty
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
