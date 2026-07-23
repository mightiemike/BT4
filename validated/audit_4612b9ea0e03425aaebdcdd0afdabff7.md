Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of economic actor, enabling full allowlist bypass — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap()` call — the direct caller. When `MetricOmmSimpleRouter` intermediates a swap, the direct caller is the router, not the user. Allowlisting the router to enable router-mediated swaps for legitimate users simultaneously opens the gate to every user, fully defeating the curation policy. There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`: [2](#0-1) 

The pool passes its own `msg.sender` as `sender` to `_beforeSwap`. When `MetricOmmSimpleRouter.exactInputSingle` executes:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [3](#0-2) 

the pool's `msg.sender` is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, never seeing the originating user's address. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (the adder contract) and checks `owner` — the economic actor — demonstrating the correct pattern is already understood and applied on the liquidity side but not replicated on the swap side. [5](#0-4) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses its curation guarantee the moment any non-allowlisted user routes through `MetricOmmSimpleRouter`. The pool's LP funds are exposed to trades from actors the pool admin explicitly excluded. This constitutes broken core pool functionality: the allowlist extension's sole purpose — restricting who may swap — is structurally unenforceable when the primary swap interface is used. The dual failure mode (allowlist router → all users bypass; don't allowlist router → legitimate users locked out of primary UX) leaves no valid configuration.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any non-allowlisted user rejected by a direct pool call will naturally retry through the router. The bypass requires no privileged access, no special token, and no multi-step setup — a single `exactInput` or `exactInputSingle` call suffices. Pool admins who allowlist the router to restore usability for legitimate users unknowingly open the gate to all users.

## Recommendation
The extension must check the economic actor, not the intermediary. Two sound approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and verifies it. This is acceptable given the router is a protocol-controlled contract.
2. **Extend the swap interface with an `originalSender` field**: Analogous to how `owner` is already separated from `sender` in the liquidity hooks, carry the originating user address separately from the direct caller in the swap path.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is meant to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` so `userA` can use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. Router calls `pool.swap(recipient=userB, ...)` — pool's `msg.sender` is the router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true`.
8. `userB`'s swap executes on the supposedly curated pool.

Removing the router from the allowlist (step 3) prevents step 8 but simultaneously causes `userA`'s router swaps to revert — there is no valid configuration.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-165)
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
