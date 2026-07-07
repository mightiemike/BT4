### Title
Unprotected `initialize()` on `WithdrawPool` Proxy Allows Front-Running to Seize Ownership and Drain Funds — (`core/contracts/WithdrawPool.sol`)

---

### Summary

`WithdrawPool.initialize()` is an unrestricted `external` function with no caller check. The only guard is the `initializer` modifier (one-time execution). If the proxy is deployed in a transaction that does not atomically call `initialize`, any attacker can front-run the initialization, become owner, and set attacker-controlled `clearinghouse` and `verifier` addresses — enabling full fund drainage.

---

### Finding Description

`WithdrawPool` exposes a bare `initialize` with no access control: [1](#0-0) 

It delegates to `BaseWithdrawPool._initialize`, which calls `__Ownable_init()` — setting `msg.sender` as owner — and writes `clearinghouse` and `verifier` from caller-supplied arguments: [2](#0-1) 

The `_disableInitializers()` call in the constructor only locks the **implementation contract's** storage slot. The proxy is a separate contract with its own fresh storage (`initialized = 0`), so the `initializer` modifier does not block a first call on the proxy: [3](#0-2) 

Contrast this with `ContractOwner.initialize`, which explicitly enforces `require(_deployer == msg.sender, "expected deployed to initialize")` before proceeding — `WithdrawPool` has no equivalent guard.

---

### Impact Explanation

Once an attacker controls `owner` and `clearinghouse`:

1. **`removeLiquidity` drain** — gated only by `onlyOwner`. Attacker calls it directly to transfer any token balance held by the pool to an arbitrary address: [4](#0-3) 

2. **`submitWithdrawal` abuse** — gated only by `require(msg.sender == clearinghouse)`. Attacker's fake clearinghouse calls it with arbitrary `token`, `sendTo`, and `amount`, bypassing all signature/quorum validation: [5](#0-4) 

3. **`submitFastWithdrawal` signature bypass** — `verifier` is set to attacker's contract; `v.requireValidTxSignatures(...)` can be made to always pass, allowing attacker to drain the pool via the fast-withdrawal path: [6](#0-5) 

---

### Likelihood Explanation

The precondition is a non-atomic deployment: proxy deployed in one transaction, `initialize` called in a subsequent transaction. This is a common multi-step deployment pattern (e.g., deploy all proxies first, then configure). Any mempool observer can detect the uninitialized proxy and front-run the initialization call. No privileged access, leaked keys, or social engineering is required — only a standard `eth_sendRawTransaction`.

---

### Recommendation

Add a deployer/caller restriction to `initialize`, mirroring the pattern already used in `ContractOwner`:

```solidity
// WithdrawPool.sol
function initialize(address _clearinghouse, address _verifier) external {
    require(msg.sender == _expectedDeployer, "unauthorized initializer");
    _initialize(_clearinghouse, _verifier);
}
```

Alternatively, pass initialization calldata directly to the proxy constructor so deployment and initialization are atomic in a single transaction, eliminating the front-running window entirely.

---

### Proof of Concept

```solidity
// 1. Protocol deploys proxy (tx A) — initialize not yet called
WithdrawPoolProxy proxy = new WithdrawPoolProxy(implementation, proxyAdmin, "");

// 2. Attacker observes uninitialized proxy in mempool, front-runs tx B:
WithdrawPool(address(proxy)).initialize(
    address(attackerClearinghouse),
    address(attackerVerifier)
);

// 3. Assert post-conditions:
assert(WithdrawPool(address(proxy)).owner() == attacker);

// 4. Attacker drains pool:
WithdrawPool(address(proxy)).removeLiquidity(productId, poolBalance, attacker);

// 5. OR: attacker's clearinghouse calls submitWithdrawal directly:
attackerClearinghouse.callSubmitWithdrawal(
    address(proxy), token, attacker, poolBalance, 0
);
```

### Citations

**File:** core/contracts/WithdrawPool.sol (L15-18)
```text
contract WithdrawPool is BaseWithdrawPool {
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L18-21)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L23-30)
```text
    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L90-91)
```text
        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L151-157)
```text
    function removeLiquidity(
        uint32 productId,
        uint128 amount,
        address sendTo
    ) external onlyOwner {
        handleWithdrawTransfer(getToken(productId), sendTo, amount);
    }
```
