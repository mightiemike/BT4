Confirmed: `ExchangeInjectActuator` and `ExchangeWithdrawActuator` restrict the caller to the exchange creator only ("is not the creator" check), so any *unprivileged* attacker cannot inject/withdraw liquidity in someone else's TRX/TRC10 bonding-curve pool. That removes the direct sandwich vector (front-run with inject/withdraw) for those two contract types.

However, `ExchangeTransactionContract`/`ExchangeTransactionActuator` has no such restriction — **any account** can submit a buy/sell trade against any exchange pool. This means an unprivileged attacker can still front-run and back-run a victim's `ExchangeTransactionContract` with their own `ExchangeTransactionContract` trades on the same pool, moving the Bancor relay curve's spot ratio before the victim's trade executes and restoring it afterward, which is the same class of attack described in the report (spot-price-based swap sizing manipulated via same-pool trades, "slippage control" is only a client-computed minimum output).

### Title
On-chain TRX/TRC10 Exchange computes swap output purely from manipulable spot reserve ratio, enabling sandwich attacks against `ExchangeTransactionContract` - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The Bancor-relay based TRX/TRC10 `Exchange` feature computes the amount a trader receives entirely from the exchange's current on-chain reserve balances (`firstTokenBalance`/`secondTokenBalance`), i.e., the pool's instantaneous spot price, with no TWAP or external oracle. The only protection against price manipulation is the trader-supplied `expected` field, a client-side minimum-output value analogous to `_applySlippage`/`amountInMaximum` in the reported bug — computed off-chain against a stale spot price and therefore itself manipulable by a same-block sandwich.

### Finding Description
`ExchangeCapsule.transaction()` computes `buyTokenQuant` directly from the current pool reserves via `ExchangeProcessor.exchange()` / `SafeExchangeProcessor.exchange()`, which apply the Bancor relay formula against `firstTokenBalance`/`secondTokenBalance` read at call time: [1](#0-0) 

The core pricing math is purely a function of the current reserve ratio (spot price), with no time-weighting or oracle input: [2](#0-1) 

`ExchangeTransactionActuator.doValidate()` enforces only that the computed output is not less than the caller-supplied `tokenExpected`, which is the sole "slippage control", entirely analogous to `_applySlippage(amountInMaximum)` in the reported Uniswap-based contract: [3](#0-2) 

`ExchangeTransactionContract` (unlike `ExchangeInjectContract`/`ExchangeWithdrawContract`, which are restricted to the exchange creator) can be submitted by **any account** against **any** exchange pool: `execute()` simply looks up the exchange by ID and applies the trade using the caller's own `ownerAddress`, with no creator/permission check: [4](#0-3) 

Because a trader's `tokenExpected` is computed off-chain from the spot reserve ratio observed before broadcasting, and any other unprivileged account can submit their own `ExchangeTransactionContract` transactions on the same pool in the same or an adjacent block, an attacker can:
1. Observe a pending victim `ExchangeTransactionContract`.
2. Front-run it with a large trade in the same direction to shift `firstTokenBalance`/`secondTokenBalance` unfavorably for the victim.
3. Let the victim's trade execute at the worsened spot price — it still passes validation as long as the output is `>= tokenExpected` (which the victim set generously or based on stale data).
4. Back-run with an opposite trade to restore the pool and capture the price difference, extracting value from the victim exactly as in the reported spot-price sandwich pattern.

### Impact Explanation
A successful sandwich directly transfers value from the victim trader to the attacker via the pool's bonding-curve reserves, i.e., concrete accounting loss for TRX/TRC10 asset holders using the Exchange feature, mirroring the "High" impact rated in the source report (loss to the party relying on the spot-price-derived swap amount).

### Likelihood Explanation
Likelihood is Medium: block producers control transaction ordering within a block and the mempool is public, so an attacker (or a colluding/self witness) can order transactions to sandwich a victim's `ExchangeTransactionContract`; no privileged role is required to submit the attacking trades since `ExchangeTransactionContract` has no creator/permission restriction, unlike inject/withdraw operations.

### Recommendation
Add on-chain slippage protection that is resistant to same-block manipulation, e.g.: (1) enforce a maximum allowed price-impact percentage per single `ExchangeTransactionContract` based on the pre-trade committed price rather than solely a user-supplied minimum, (2) introduce a TWAP or multi-block price checkpoint for the reserve ratio used in fee/slippage decisions, or (3) rate-limit/cap the trade size relative to pool depth per block to blunt front-run/back-run round-trips.

### Proof of Concept
1. Attacker observes a pending `ExchangeTransactionContract` from Victim selling `tokenA` for `tokenB` on exchange `E`.
2. Attacker submits `ExchangeTransactionContract` selling a large amount of `tokenA` for `tokenB` on `E`, executed via `ExchangeTransactionActuator.execute()` → `ExchangeCapsule.transaction()`, shifting `firstTokenBalance/secondTokenBalance` and worsening the effective spot price for subsequent `tokenA→tokenB` sellers.
3. Victim's transaction executes next in the block; `doValidate()` only checks `anotherTokenQuant >= tokenExpected` [3](#0-2) , so it succeeds despite receiving less `tokenB` than it would have at the undisturbed price.
4. Attacker submits a reverse `ExchangeTransactionContract` (buy back `tokenA` with `tokenB`) restoring the pool and realizing the spread as profit, all using the unprivileged `ExchangeTransactionContract` path with no creator restriction [5](#0-4) .

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-145)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

    long buyTokenQuant = 0;
    long firstTokenBalance = this.exchange.getFirstTokenBalance();
    long secondTokenBalance = this.exchange.getSecondTokenBalance();
    long newFirstTokenBalance;
    long newSecondTokenBalance;

    if (this.exchange.getFirstTokenId().equals(ByteString.copyFrom(sellTokenID))) {
      buyTokenQuant = processor.exchange(firstTokenBalance,
          secondTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(firstTokenBalance, sellTokenQuant)
          : firstTokenBalance + sellTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(secondTokenBalance, buyTokenQuant)
          : secondTokenBalance - buyTokenQuant;
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L38-70)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) object;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    try {
      final ExchangeTransactionContract exchangeTransactionContract = this.any
          .unpack(ExchangeTransactionContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeTransactionContract.getOwnerAddress().toByteArray());

      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));

      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
