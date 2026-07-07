### Title
Unprotected `initialize` in `Verifier.sol` Allows Front-Run Takeover of Protocol-Wide Signature Verification — (File: `core/contracts/Verifier.sol`)

---

### Summary

`Verifier.initialize` has no caller restriction and no validation of the supplied public-key set. An attacker who front-runs the deployment initialization transaction becomes the owner of `Verifier` and controls every public key used to authorize protocol transactions, enabling forged fast-withdrawal signatures and direct asset theft from `WithdrawPool`.

---

### Finding Description

`Verifier.initialize` is marked `external initializer` with no `msg.sender` guard and no check that the supplied `Point[8] initialSet` values are valid curve points or non-zero. [1](#0-0) 

Because the `initializer` modifier (OpenZeppelin) only prevents a second call, the very first caller wins ownership and sets the canonical public-key set. Any unprivileged attacker who observes the deployment transaction in the mempool can submit the same call with higher gas, supplying either all-zero points (leaving `nSigner = 0`) or attacker-controlled elliptic-curve points.

After a successful front-run the attacker:
1. Becomes `owner` of `Verifier` (via `__Ownable_init()`).
2. Controls `pubkeys[0..7]` and `nSigner`.
3. Can call `assignPubKey` at will to rotate keys to any value. [2](#0-1) 

The legitimate deployer's `initialize` call then reverts because the `initializer` guard has already been consumed.

---

### Impact Explanation

`WithdrawPool.submitFastWithdrawal` delegates all signature authority to `Verifier`: [3](#0-2) 

With attacker-controlled keys registered in `Verifier`, the attacker can produce signatures that pass `requireValidTxSignatures`, craft arbitrary `WithdrawCollateral` or `WithdrawCollateralV2` payloads, and call `submitFastWithdrawal` to drain every ERC-20 token held by `WithdrawPool`.

Additionally, `Endpoint.initialize` stores `verifier` as an immutable reference: [4](#0-3) 

All downstream transaction verification (liquidations, transfers, NLP mint/burn) flows through the same compromised `Verifier`, giving the attacker protocol-wide signature forgery capability.

---

### Likelihood Explanation

The attack requires only:
- Monitoring the public mempool for the proxy `initialize` calldata (trivially observable).
- Resubmitting the same call with higher gas before the deployer's transaction is mined.

No privileged access, leaked keys, or social engineering is needed. This is a standard front-run pattern executable by any EOA on any EVM chain where Nado is deployed.

---

### Recommendation

Add a caller restriction to `Verifier.initialize` so only the deploying factory or a known address can invoke it, and validate that at least one supplied point is non-zero (i.e., `nSigner > 0` after initialization):

```solidity
function initialize(address _expectedCaller, Point[8] memory initialSet)
    external
    initializer
{
    require(msg.sender == _expectedCaller, "Invalid caller");
    __Ownable_init();
    uint256 count;
    for (uint256 i = 0; i < 8; ++i) {
        if (!isPointNone(initialSet[i])) {
            _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            count++;
        }
    }
    require(count > 0, "No valid signers provided");
}
```

Apply the same pattern to every other unguarded `initialize` in the codebase (`OffchainExchange`, `Endpoint`, `Clearinghouse`, `SpotEngine`, `PerpEngine`, `WithdrawPool`, `Airdrop`, `BaseProxyManager`). [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Proof of Concept

```
1. Deployer broadcasts: Verifier_proxy.initialize([validPoints...])
   (transaction visible in mempool)

2. Attacker front-runs with higher gas:
   Verifier_proxy.initialize([attackerPoints...])
   → __Ownable_init() sets owner = attacker
   → pubkeys[0] = attackerPoint  (nSigner = 1)

3. Deployer's tx reverts: "Initializable: contract is already initialized"

4. Attacker calls Verifier.assignPubKey(i, x, y) freely (onlyOwner passes).

5. Attacker crafts:
   tx = abi.encode(WithdrawCollateralV2{sender: victim, productId: USDC, amount: MAX, sendTo: attacker})
   sig = sign(tx, attackerPrivKey)   // matches pubkeys[0]

6. Attacker calls WithdrawPool.submitFastWithdrawal(idx, tx, [sig])
   → Verifier.requireValidTxSignatures passes (attacker's key is registered)
   → USDC transferred to attacker

7. Repeat for every token product in WithdrawPool.
``` [8](#0-7)

### Citations

**File:** core/contracts/Verifier.sol (L41-48)
```text
    function initialize(Point[8] memory initialSet) external initializer {
        __Ownable_init();
        for (uint256 i = 0; i < 8; ++i) {
            if (!isPointNone(initialSet[i])) {
                _assignPubkey(i, initialSet[i].x, initialSet[i].y);
            }
        }
    }
```

**File:** core/contracts/Verifier.sol (L61-67)
```text
    function assignPubKey(
        uint256 i,
        uint256 x,
        uint256 y
    ) public onlyOwner {
        _assignPubkey(i, x, y);
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-113)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```

**File:** core/contracts/Endpoint.sol (L31-66)
```text
    function initialize(
        address _sanctions,
        address _sequencer,
        address _offchainExchange,
        IClearinghouse _clearinghouse,
        address _verifier,
        address _endpointTx
    ) external initializer {
        __Ownable_init();
        __EIP712_init("Nado", "0.0.1");
        sequencer = _sequencer;
        clearinghouse = _clearinghouse;
        offchainExchange = _offchainExchange;
        verifier = IVerifier(_verifier);
        sanctions = ISanctionsList(_sanctions);
        endpointTx = _endpointTx;
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
        slowModeConfig = SlowModeConfig({timeout: 0, txCount: 0, txUpTo: 0});
        priceX18[QUOTE_PRODUCT_ID] = ONE;

        if (nlpPools.length == 0) {
            nlpPools.push(
                NlpPool({
                    poolId: 0,
                    subaccount: N_ACCOUNT,
                    owner: address(0),
                    balanceWeightX18: uint128(ONE)
                })
            );
        }
    }
```

**File:** core/contracts/OffchainExchange.sol (L243-258)
```text
    function initialize(address _clearinghouse, address _endpoint)
        external
        initializer
    {
        __Ownable_init();
        setEndpoint(_endpoint);

        __EIP712_init("Nado", "0.0.1");
        clearinghouse = IClearinghouse(_clearinghouse);
        spotEngine = ISpotEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.SPOT)
        );
        perpEngine = IPerpEngine(
            clearinghouse.getEngineByType(IProductEngine.EngineType.PERP)
        );
    }
```

**File:** core/contracts/WithdrawPool.sol (L16-18)
```text
    function initialize(address _clearinghouse, address _verifier) external {
        _initialize(_clearinghouse, _verifier);
    }
```
