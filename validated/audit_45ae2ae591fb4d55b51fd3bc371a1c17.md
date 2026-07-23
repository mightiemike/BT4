Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any unprivileged user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the router is allowlisted — not the end user. Any pool admin who allowlists the router (required for any router-mediated swap on a curated pool) inadvertently grants every unprivileged user the ability to bypass the per-user allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` with itself as `msg.sender`, passing `params.extensionData` (user-supplied, not encoding the real caller): [3](#0-2) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. The allowlist storage and setter operate on individual addresses with no mechanism to recover the original caller: [4](#0-3) 

Once the router is allowlisted, the check passes for every call arriving through the router regardless of who the actual end user is. The end user's identity is never examined.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses (e.g., KYC-verified counterparties, whitelisted market makers, or regulatory-compliant participants) loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. The attacker receives the full output token amount from the pool without being on the allowlist. This is a direct bypass of the pool's access-control invariant: the pool transacts with counterparties the admin explicitly excluded, and LP value is exposed to unintended swap flow. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path circumvents the pool admin's access control.

## Likelihood Explanation
The scenario requires the pool admin to have added the router to `allowedSwapper[pool]`. This is a natural and expected administrative action — without it, no allowlisted user can use the router either, making the router useless for that pool. Any pool that intends to support router-mediated swaps while also enforcing a per-user allowlist will reach this configuration. The attacker needs only to call the public router with a valid swap; no special privileges, flash loans, or oracle manipulation are required. The condition is reachable by any unprivileged trader.

## Recommendation
The extension must verify the ultimate end user, not the intermediary. The cleanest fix is to require the router to encode the actual `msg.sender` (the end user) into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of (or in addition to) `sender`. The pool already forwards `extensionData` unmodified to every extension hook, so no core changes are needed. Alternatively, the extension could maintain a registry of trusted routers and, when `sender` is a trusted router, require the decoded user address from `extensionData` to be allowlisted.

## Proof of Concept
```
Setup:
  Pool P has SwapAllowlistExtension E configured
  Admin allowlists router R: allowedSwapper[P][R] = true
  User Alice (0xAlice) is NOT in allowedSwapper[P]

Attack:
  Alice calls MetricOmmSimpleRouter.exactInputSingle({
    pool: P,
    tokenIn: token0,
    extensionData: "",   // no real-user encoding
    ...
  })

  Router calls P.swap(recipient, zeroForOne, amount, limit, "", "")
    → msg.sender = Router (R)

  Pool calls _beforeSwap(sender=R, ...)
  Extension checks: allowedSwapper[P][R] == true  ✓  (check passes)

  Swap executes; Alice receives output tokens.

Result:
  Alice, who is not on the allowlist, successfully swaps on a curated pool.
  The allowlist guard is fully bypassed.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension
  2. Admin calls setAllowedToSwap(pool, router, true)
  3. Confirm Alice is NOT allowlisted: isAllowedToSwap(pool, alice) == false
  4. Alice calls router.exactInputSingle(...) — expect success (no revert)
  5. Assert Alice received output tokens despite not being on the allowlist
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-29)
```text
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
