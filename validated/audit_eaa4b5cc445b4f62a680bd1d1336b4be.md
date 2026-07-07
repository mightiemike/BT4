### Title
Silent ETH Loss via Low-Level Call to Potentially Non-Existent `wrappedNative` in `receive()` — (File: `core/contracts/DirectDepositV1.sol`)

---

### Summary
`DirectDepositV1.receive()` forwards ETH to `wrappedNative` using a bare low-level call and guards only with `require(success, ...)`. Per EVM design, a low-level `call` to a non-existent account returns `success = true`. No contract-existence check is performed on `wrappedNative` before or after the call, so if `wrappedNative` holds no code, the require passes silently while the caller's ETH is permanently lost.

---

### Finding Description

In `DirectDepositV1.sol`, the `receive()` function is:

```solidity
receive() external payable {
    (bool success, ) = wrappedNative.call{value: msg.value}("");
    require(success, "Failed to wrap native token.");
}
```

The only liveness guard is `require(success, ...)`. However, the Solidity documentation explicitly states: *"The low-level functions `call`, `delegatecall` and `staticcall` return `true` as their first return value if the account called is non-existent, as part of the design of the EVM."*

`wrappedNative` is set once in the constructor and is never validated to be a live contract:

```solidity
constructor(
    address _endpoint,
    address _spotEngine,
    bytes32 _subaccount,
    address payable _wrappedNative
) {
    ...
    wrappedNative = _wrappedNative;
    ...
}
```

`ContractOwner.createDirectDepositV1()` passes its own stored `wrappedNative` directly into every new DDA:

```solidity
DirectDepositV1 directDepositV1 = new DirectDepositV1{salt: bytes32(uint256(1))}(
    address(endpoint), address(spotEngine), subaccount, wrappedNative
);
```

No `code.length` check exists anywhere in `DirectDepositV1.sol` for `wrappedNative` — confirmed by the absence of any such guard in the codebase.

If `wrappedNative` is a non-existent address (misconfigured EOA, zero address, or a self-destructed WETH contract), then:
- `wrappedNative.call{value: msg.value}("")` returns `(true, "")`.
- `require(success, ...)` passes.
- The ETH is sent to a dead address; no WETH is minted.
- The transaction does not revert.

---

### Impact Explanation
Any user who sends ETH to a DDA whose `wrappedNative` is non-existent permanently loses their ETH. No WETH is credited, no revert occurs, and the funds are irrecoverable. This is a direct, irreversible loss of user assets reachable by any unprivileged caller who sends ETH to the DDA.

---

### Likelihood Explanation
`ContractOwner` is initialized once with a `wrappedNative` address that is propagated to every DDA ever created. If that address is wrong at initialization time (e.g., an EOA, a zero address, or a future chain migration where the WETH contract is redeployed at a different address), every DDA silently destroys ETH on receipt. The constructor also runs a similar call at line 56 and explicitly acknowledges the risk of failure — yet `receive()` at line 65 applies no equivalent defensive pattern. The likelihood is low under correct deployment but the impact is total and irreversible when triggered.

---

### Recommendation
Add a contract-existence check before the low-level call in `receive()`:

```solidity
receive() external payable {
    require(wrappedNative.code.length > 0, "wrappedNative is not a contract");
    (bool success, ) = wrappedNative.call{value: msg.value}("");
    require(success, "Failed to wrap native token.");
}
```

Alternatively, use a typed WETH interface (`IWETH(wrappedNative).deposit{value: msg.value}()`) which will revert on a non-existent target. The same existence check should be applied in the constructor's balance-forwarding block (line 56) for consistency.

---

### Proof of Concept

1. `ContractOwner` is initialized with `wrappedNative = 0xdead...` (an EOA or self-destructed contract).
2. Any caller invokes `ContractOwner.creditDepositV1(subaccount)`, which deploys a new `DirectDepositV1` with `wrappedNative = 0xdead...`.
3. The caller (or any user) sends 1 ETH to the DDA address.
4. `receive()` fires: `wrappedNative.call{value: 1 ether}("")` returns `(true, "")` — the EVM returns success for a call to a codeless account.
5. `require(success, "Failed to wrap native token.")` passes.
6. 1 ETH is permanently lost. No WETH is minted. No revert. No event indicating failure.

**Root cause line:** [1](#0-0) 

**`wrappedNative` set without existence validation:** [2](#0-1) 

**Propagation from `ContractOwner`:** [3](#0-2)

### Citations

**File:** core/contracts/DirectDepositV1.sol (L46-51)
```text
        address payable _wrappedNative
    ) {
        endpoint = IIEndpoint(_endpoint);
        spotEngine = IISpotEngine(_spotEngine);
        subaccount = _subaccount;
        wrappedNative = _wrappedNative;
```

**File:** core/contracts/DirectDepositV1.sol (L64-67)
```text
    receive() external payable {
        (bool success, ) = wrappedNative.call{value: msg.value}("");
        require(success, "Failed to wrap native token.");
    }
```

**File:** core/contracts/ContractOwner.sol (L495-498)
```text
        DirectDepositV1 directDepositV1 = new DirectDepositV1{
            salt: bytes32(uint256(1))
        }(address(endpoint), address(spotEngine), subaccount, wrappedNative);
        directDepositV1Address[subaccount] = payable(directDepositV1);
```
