Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. The extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Any non-allowlisted user can bypass the per-user gate by calling the router, which the admin must allowlist to support any router-mediated swaps, making the allowlist completely ineffective for router flows.

## Finding Description
**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract address.

**Step 2 — Extension checks `sender` (the router), not the actual user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` and evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [2](#0-1) 

The effective check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Step 3 — Router calls the pool directly with no originator forwarding.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The original `msg.sender` is stored only in transient callback context for payment purposes, never forwarded to the pool or extension: [3](#0-2) 

The same applies to `exactInput` (L103-112) and `exactOutput`/`exactOutputSingle` paths. [4](#0-3) 

**Step 4 — The admin faces an impossible dilemma.**

- If the admin does **not** allowlist the router: individually allowlisted users who use the router are blocked.
- If the admin **does** allowlist the router: every user — including those explicitly excluded — can bypass the allowlist by routing through the router.

No configuration simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users, because the router erases the caller's identity.

## Impact Explanation
A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to a specific set of addresses (e.g., KYC'd users, protocol partners). Any non-allowlisted user can bypass this control by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`). The extension approves the swap because it sees the allowlisted router address as `sender`. The disallowed user receives output tokens from the pool, violating the pool's access policy. This constitutes an admin-boundary break — the allowlist guard is rendered completely ineffective for router-mediated swaps. [5](#0-4) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical, publicly deployed periphery contract. Pool admins who want to support normal user flows must allowlist it. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any user can call `exactInputSingle` on a curated pool. Likelihood is high whenever a curated pool also permits router access.

## Recommendation
The extension must check the economically relevant actor, not the intermediary. The cleanest fix: the router should forward `msg.sender` as `originator` in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and verify it against the allowlist instead of (or in addition to) `sender`. This requires a coordinated interface change between the router and extension. Alternatively, check `recipient` instead of `sender` if the pool's threat model is about who receives output.

## Proof of Concept
```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so legitimate users can swap via router
extension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT individually allowlisted
address alice = makeAddr("alice");
// allowedSwapper[pool][alice] == false

// Alice bypasses the allowlist by routing through the router
vm.prank(alice);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: alice,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Alice successfully swaps despite not being on the allowlist.
// Extension checked allowedSwapper[pool][router] == true, not allowedSwapper[pool][alice].
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-13)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
