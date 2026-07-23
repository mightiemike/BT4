Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Non-Allowlisted Users to Bypass the Swap Guard via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the originating EOA. If the pool admin allowlists the router to permit legitimate router-mediated swaps, every non-allowlisted address can bypass the guard by calling the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool — i.e., whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router contract the `msg.sender` of that call, not the originating EOA: [3](#0-2) 

The same pattern applies to `exactInput` (intermediate hops use `address(this)`) and `exactOutputSingle`: [4](#0-3) 

The admin faces an inescapable dilemma: not allowlisting the router blocks all legitimate router users; allowlisting the router opens the guard to every address. There is no mechanism for the router to forward the originating EOA to the extension — `extensionData` is forwarded but `SwapAllowlistExtension` ignores it entirely.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument, which is explicitly supplied by the caller and set to the actual depositor by `LiquidityAdder`, so the deposit guard does not share this flaw.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd institutions, whitelisted market makers) is rendered entirely ineffective once the router is allowlisted. Non-allowlisted users can execute swaps against LP positions that were priced and sized under the assumption of a restricted counterparty set. This constitutes a broken core pool access-control mechanism and an admin-boundary break: the pool admin's explicit restriction is bypassed by an unprivileged path through the canonical periphery router.

## Likelihood Explanation
The router is the canonical entry point for end-users. Any pool operator who deploys `SwapAllowlistExtension` and also wants legitimate users to access the pool through the router must allowlist the router, triggering the bypass. The attacker requires no special privilege — a single call to `exactInputSingle` on the router suffices, and the condition is met by any pool that uses both `SwapAllowlistExtension` and the standard router.

## Recommendation
Pass the originating caller through the extension interface rather than the immediate `msg.sender` of `pool.swap()`. Two concrete options:

1. **Encode originator in `extensionData`**: Define a convention where the router ABI-encodes the real caller (`msg.sender`) into `extensionData`; `SwapAllowlistExtension` decodes and checks that address when present, falling back to `sender` for direct calls.
2. **Add an `originator` field to the swap interface**: Extend `pool.swap()` with an explicit originator parameter so the router can forward `msg.sender` to the pool, which then passes it to `beforeSwap` as a distinct argument.

Additionally, document clearly that `SwapAllowlistExtension` only gates direct `pool.swap()` callers and is **not** effective when the router is allowlisted.

## Proof of Concept
```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists alice (legitimate user) and the router (so alice can use it).
swapAllowlist.setAllowedToSwap(address(pool), alice, true);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// bob is NOT allowlisted.
// Direct swap by bob reverts:
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, false, int128(1000), type(uint128).max, "", "");

// Router-mediated swap by bob succeeds — router is allowlisted, bob's identity is never checked:
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        tokenIn: token1,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// bob successfully swapped on a pool he was not allowlisted for.
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
