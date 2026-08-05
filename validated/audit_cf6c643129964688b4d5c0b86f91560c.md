Confirmed: `ExchangeInjectContract` in `protocol/src/main/protos/core/contract/exchange_contract.proto:17-22` has only `owner_address`, `exchange_id`, `token_id`, and `quant` — no minimum/expected parameter at all, unlike `ExchangeTransactionContract` which has the `expected` field used for a slippage check at [1](#0-0) .

### Title
Missing slippage protection in ExchangeInject (liquidity add) allows sandwich-attack value extraction - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
TRON's Bancor-style `Exchange`/`ExchangeV2` AMM lets users inject liquidity via `ExchangeInjectContract`, which is processed by `ExchangeInjectActuator`. Unlike the sibling `ExchangeTransactionContract` (swap), which carries an `expected` minimum-output field that is checked against the computed output before committing, `ExchangeInjectContract` has no analogous parameter, and neither `doValidate` nor `execute` in `ExchangeInjectActuator` enforce any bound on the required "another token" amount computed from the live pool ratio. [2](#0-1) [3](#0-2) 

### Finding Description
`ExchangeInjectActuator.doValidate`/`execute` compute the paired-token amount required to inject liquidity strictly from the exchange pool's current `firstTokenBalance`/`secondTokenBalance` ratio at validation/execution time: `anotherTokenQuant = floorDiv(multiplyExact(otherBalance, tokenQuant), sameSideBalance)`. [4](#0-3)  There is no user-supplied bound (like `expected` in swaps) that caps how much of the paired token the depositor is willing to contribute, nor any check that the resulting deposit ratio matches the depositor's expectation. This is directly analogous to the reported Uniswap v4-periphery issue: the deposit-side accounting can silently diverge from what the user intended if the pool ratio has been moved beforehand, and there is no slippage gate to catch it — the periphery report's root cause (increasing a liquidity position without enforcing a bound on the token amounts consumed) maps to `ExchangeInjectContract`/`ExchangeInjectActuator` lacking any minimum/maximum parameter at all, which is a stronger absence than the periphery bug (which only failed to check the *positive*-delta case). [5](#0-4) 

A miner/validator or any actor able to order transactions within a block can front-run a victim's `ExchangeInjectContract` with an `ExchangeTransactionContract` (swap) that skews `firstTokenBalance`/`secondTokenBalance`, causing the victim's injection to be executed at a ratio unfavorable to the victim (they contribute proportionally more of the token they're injecting relative to what they'd get if the ratio reverts), and then back-run with a reverse swap to restore the ratio and extract the discrepancy, analogous to the sandwich attack described in the report. Because `ExchangeCapsule.setBalance` and the actuator update pool balances purely by the live ratio with no depositor-specified guard, this is enforceable every time. [6](#0-5) 

### Impact Explanation
A liquidity depositor using `ExchangeInjectContract` can lose value relative to fair pricing whenever the pool ratio is manipulated immediately before their transaction executes, exactly as described in the underlying report ("the liquidity depositor still loses value as their... position... is worth less than the pre-deposit value"). This is a public, unprivileged-user-facing accounting/economic-loss issue in the on-chain AMM exchange feature, not a mocked or internal-only path — any TRON account can call `ExchangeInjectContract`.

### Likelihood Explanation
Exploitability requires an actor capable of ordering or injecting transactions before the victim's `ExchangeInjectContract` executes within the same block (e.g., a block-producing witness, or an attacker racing transactions into the same block), which is a realistic threat model for AMM sandwich attacks on any chain, including TRON. The `ExchangeTransactionContract` swap path already demonstrates the intended mitigation pattern (`expected` field checked in `doValidate`) exists elsewhere in the same actuator family but was simply never applied to the injection path. [1](#0-0) 

### Recommendation
Add a bound (e.g., `expected_another_quant` max/min) to `ExchangeInjectContract`, and enforce it in `ExchangeInjectActuator.doValidate`/`execute` by comparing the computed `anotherTokenQuant` against the caller-supplied bound before committing balance changes, mirroring the existing `tokenExpected` check pattern in `ExchangeTransactionActuator`. [1](#0-0) 

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` from victim in the transaction pool/network specifying `token_id=A`, `quant=100`.
2. Attacker submits an `ExchangeTransactionContract` swap that shifts the pool's `firstTokenBalance`/`secondTokenBalance` ratio (e.g., sells a large amount of token B into the pool), ordered before the victim's transaction in the same block.
3. Victim's `ExchangeInjectActuator.execute` computes `anotherTokenQuant` from the now-skewed ratio via `floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance)`, forcing the victim to contribute an unfavorable amount of token B for their 100 units of token A, with no `expected`/bound field to reject the unfavorable outcome. [7](#0-6) 
4. Attacker submits a reverse swap after the victim's injection, restoring the pool ratio and capturing the value extracted from the victim's mispriced deposit.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-36)
```text
message ExchangeInjectContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeWithdrawContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeTransactionContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
  int64 expected = 5;
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L65-83)
```java
      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L125-169)
```java
  private boolean doValidate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    if (!this.any.is(ExchangeInjectContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [ExchangeInjectContract],real type[" + any
              .getClass() + "]");
    }
    final ExchangeInjectContract contract;
    try {
      contract = this.any.unpack(ExchangeInjectContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!accountStore.has(ownerAddress)) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] not exists");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule.getBalance() < calcFee()) {
      throw new ContractValidateException("No enough balance for exchange inject fee!");
    }

    ExchangeCapsule exchangeCapsule;
    try {
      exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(contract.getExchangeId()));

```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L106-112)
```java
  public long getFirstTokenBalance() {
    return this.exchange.getFirstTokenBalance();
  }

  public long getSecondTokenBalance() {
    return this.exchange.getSecondTokenBalance();
  }
```
