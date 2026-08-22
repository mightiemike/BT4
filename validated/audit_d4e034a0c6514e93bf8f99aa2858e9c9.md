### Title
PBFT cursor offset silently falls back to an unintended (too-recent) snapshot when the requested historical depth exceeds the in-memory stack size - (File: chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java)

### Summary
`Chainbase.head()` (the java-tron analog of cosmos-sdk's `LoadVersionAndUpgrade`) is supposed to resolve a specific historical database version when the `PBFT` cursor is active, using an `offset` computed from `headBlockNum - latestPbftBlockNum`. When the requested offset exceeds the number of in-memory snapshots actually retained, the walk-back loop silently stops at the current root snapshot instead of erroring or reaching the true requested version, so the query executes against the wrong (more recent, non-PBFT-finalized) state without any indication to the caller.

### Finding Description
`Manager.setCursor` computes the offset needed to reach the PBFT-confirmed block from HEAD and passes it down to the revoking store: [1](#0-0) 

That offset is forwarded to every `Chainbase` instance via `SnapshotManager.setCursor(cursor, offset)`: [2](#0-1) 

`Chainbase.head()` then walks backward from the in-memory `head` snapshot `offset` times to find the snapshot corresponding to the PBFT-confirmed block: [3](#0-2) 

The loop condition `i < offset.get() && tmp != tmp.getRoot()` stops early — without any error, warning, or exception — as soon as it reaches the current root snapshot, even if `i` has not reached `offset`. The root is *not* the block that is `offset` versions behind HEAD; it is simply the oldest snapshot still retained in memory before the last flush to persistent storage. The number of snapshots retained in memory is bounded by `DEFAULT_STACK_MAX_SIZE = 256` (or a configured smaller value), and once `size > maxSize` a flush collapses/prunes older snapshots into the root: [4](#0-3) [5](#0-4) 

Thus, whenever `headBlockNum - latestPbftBlockNum > maxSize` (i.e., PBFT confirmation has lagged behind HEAD by more than the retained in-memory window), the offset argument is effectively disregarded: instead of reaching the state that corresponds to the actually PBFT-agreed block, the query silently resolves to whatever snapshot happens to be at the root of the in-memory chain (a state much closer to HEAD than requested) — exactly the "argument disregarded, wrong version served" bug class described in the source report for `LoadVersionAndUpgrade`.

This cursor/offset mechanism is reachable from anonymous network requests: any client hitting a PBFT-scoped HTTP/gRPC/JSON-RPC endpoint routes through `WalletOnPBFT`/`WalletOnCursor.futureGet`, which calls `Manager.setCursor(Cursor.PBFT)` before executing the query: [6](#0-5) [7](#0-6) 

This covers a large surface of unprivileged, anonymous PBFT-node RPC endpoints (`getaccount`, `triggerconstantcontract`, `estimateenergy`, `getblockbynum`, `gettransactionbyid`, `isspend`, market/exchange queries, etc.), all registered in the PBFT servlet context: [8](#0-7) 

### Impact Explanation
The PBFT read path exists specifically to give clients a finality guarantee stronger than plain HEAD reads (protection against short-range forks/reorgs). If PBFT confirmation stalls (e.g., due to witness unavailability, network partition, or an active attack on consensus finality) for longer than the retained in-memory snapshot window, every PBFT-scoped query (balances, `triggerconstantcontract`/`estimateenergy` results, `isspend`, exchange/market prices, delegated resource queries, etc.) will silently be served from a snapshot that is not the block the client asked to be confirmed against. Callers (exchanges, wallets, bridges) that rely on the PBFT interface specifically to avoid acting on non-final state can be misled into treating unconfirmed or reorg-able state as final, resulting in accounting/consensus-view inconsistency and potential double-spend-style exposure for downstream consumers, with no error surfaced to detect the discrepancy.

### Likelihood Explanation
This requires PBFT confirmation to lag HEAD by more than the in-memory retention window (`DEFAULT_STACK_MAX_SIZE = 256` snapshots, or an operator-configured smaller `maxFlushCount`/`maxSize`), which is not the normal steady-state but is a realistic scenario during network stress, partial partitions, or witness downtime — situations attackers can deliberately try to induce or exploit. Because the fallback is silent (no exception, no log distinguishing "reached requested offset" vs. "hit root early"), the condition is likely to go unnoticed in production and is not gated by any privileged access — it is triggerable purely by the passage of an adverse consensus condition combined with ordinary anonymous PBFT API calls.

### Recommendation
In `Chainbase.head()`, detect when the walk-back loop terminates due to hitting `tmp.getRoot()` before `i` reaches `offset`, and treat this as an error condition (throw/propagate a distinct exception, e.g. `ItemNotFoundException`/a new "snapshot unavailable" exception) rather than silently returning the root. Callers such as `Manager.setCursor`/the PBFT-scoped API layer should surface this as a request failure (e.g., "requested PBFT-confirmed state no longer available in memory") instead of transparently executing against an unintended snapshot. Consider also increasing/making configurable the retained snapshot window relative to expected PBFT confirmation lag, and adding a log/metric whenever the offset cannot be fully honored.

### Proof of Concept
Conceptual (state-dependent, no code changes required to trigger):
1. Run a PBFT-enabled full node; force PBFT block confirmation to stall (e.g., simulate insufficient witness agreement or a network partition) such that `headBlockNum - latestPbftBlockNum` grows beyond `256` (the default `DEFAULT_STACK_MAX_SIZE`) while HEAD continues advancing.
2. Issue an anonymous request to a PBFT-scoped endpoint, e.g. `POST /walletpbft/getaccount` or `/walletpbft/triggerconstantcontract`.
3. `Manager.setCursor(Cursor.PBFT)` computes `offset = headNum - pbftNum > 256` and calls `revokingStore.setCursor(PBFT, offset)`.
4. In `Chainbase.head()`, the loop `for (i=0; i<offset && tmp != tmp.getRoot(); i++) tmp = tmp.getPrevious();` exits early once `tmp == tmp.getRoot()` (after at most `size` iterations, ≤256), well before `i` reaches the requested `offset`.
5. The query is executed against the root snapshot — a state far more recent than the actually PBFT-confirmed block — with no error returned to the client, demonstrating the silently-wrong-version read.

Note: I was not able to fully trace the exact persisted-state semantics of `SnapshotRoot` (i.e., precisely how far back in block-height terms the "root" snapshot sits relative to `latestPbftBlockNum` at flush time) within the available index; this would need to be confirmed by running the node under a simulated PBFT stall to measure the exact discrepancy in served block state.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L2170-2178)
```java
  public void setCursor(Chainbase.Cursor cursor) {
    if (cursor == Chainbase.Cursor.PBFT) {
      long headNum = getHeadBlockNum();
      long pbftNum = chainBaseManager.getCommonDataBase().getLatestPbftBlockNum();
      revokingStore.setCursor(cursor, headNum - pbftNum);
    } else {
      revokingStore.setCursor(cursor);
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L50-58)
```java
  public static final int DEFAULT_MIN_FLUSH_COUNT = 1;
  private static final int DEFAULT_STACK_MAX_SIZE = 256;
  private static final long ONE_MINUTE_MILLS = 60*1000L;
  private static final String CHECKPOINT_V2_DIR = "checkpoint";
  @Getter
  private List<Chainbase> dbs = new ArrayList<>();
  @Getter
  private int size = 0;
  private AtomicInteger maxSize = new AtomicInteger(DEFAULT_STACK_MAX_SIZE);
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L119-139)
```java
  public synchronized ISession buildSession(boolean forceEnable) {
    if (disabled && !forceEnable) {
      return new Session(this);
    }

    boolean disableOnExit = disabled && forceEnable;
    if (forceEnable) {
      disabled = false;
    }

    if (size > maxSize.get() && !hitDown) {
      flushCount = flushCount + (size - maxSize.get());
      updateSolidity(size - maxSize.get());
      size = maxSize.get();
      flush();
    }

    advance();
    ++activeSession;
    return new Session(this, disableOnExit);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L146-149)
```java
  @Override
  public void setCursor(Chainbase.Cursor cursor, long offset) {
    dbs.forEach(db -> db.setCursor(cursor, offset));
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java (L70-97)
```java
  private Snapshot head() {
    if (cursor.get() == null) {
      return head;
    }

    switch (cursor.get()) {
      case HEAD:
        return head;
      case SOLIDITY:
        return head.getSolidity();
      case PBFT:
        if (offset.get() == null) {
          return head.getSolidity();
        }

        if (offset.get() >= 0) {
          Snapshot tmp = head;
          for (int i = 0; i < offset.get() && tmp != tmp.getRoot(); i++) {
            tmp = tmp.getPrevious();
          }
          return tmp;
        } else {
          return head.getSolidity();
        }
      default:
        return head;
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/WalletOnCursor.java (L16-23)
```java
  public <T> T futureGet(TronCallable<T> callable) {
    try {
      dbManager.setCursor(cursor);
      return callable.call();
    } finally {
      dbManager.resetCursor();
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/interfaceOnPBFT/WalletOnPBFT.java (L1-14)
```java
package org.tron.core.services.interfaceOnPBFT;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.tron.core.db2.core.Chainbase;
import org.tron.core.services.WalletOnCursor;

@Slf4j(topic = "API")
@Component
public class WalletOnPBFT extends WalletOnCursor {

  public WalletOnPBFT() {
    super.cursor = Chainbase.Cursor.PBFT;
  }
```

**File:** framework/src/main/java/org/tron/core/services/interfaceOnPBFT/http/PBFT/HttpApiOnPBFTService.java (L179-253)
```java
  @Override
  protected void addServlet(ServletContextHandler context) {
    // same as FullNode
    context.addServlet(new ServletHolder(accountOnPBFTServlet), "/getaccount");
    context.addServlet(new ServletHolder(listWitnessesOnPBFTServlet), "/listwitnesses");
    context.addServlet(new ServletHolder(getAssetIssueListOnPBFTServlet), "/getassetissuelist");
    context.addServlet(new ServletHolder(getPaginatedAssetIssueListOnPBFTServlet),
        "/getpaginatedassetissuelist");
    context
        .addServlet(new ServletHolder(getAssetIssueByNameOnPBFTServlet), "/getassetissuebyname");
    context.addServlet(new ServletHolder(getAssetIssueByIdOnPBFTServlet), "/getassetissuebyid");
    context.addServlet(new ServletHolder(getAssetIssueListByNameOnPBFTServlet),
        "/getassetissuelistbyname");
    context.addServlet(new ServletHolder(getNowBlockOnPBFTServlet), "/getnowblock");
    context.addServlet(new ServletHolder(getBlockByNumOnPBFTServlet), "/getblockbynum");
    context.addServlet(new ServletHolder(getDelegatedResourceOnPBFTServlet),
        "/getdelegatedresource");
    context.addServlet(new ServletHolder(getDelegatedResourceAccountIndexOnPBFTServlet),
        "/getdelegatedresourceaccountindex");
    context.addServlet(new ServletHolder(getExchangeByIdOnPBFTServlet), "/getexchangebyid");
    context.addServlet(new ServletHolder(listExchangesOnPBFTServlet), "/listexchanges");
    context.addServlet(new ServletHolder(getAccountByIdOnPBFTServlet), "/getaccountbyid");
    context.addServlet(new ServletHolder(getBlockByIdOnPBFTServlet), "/getblockbyid");
    context
        .addServlet(new ServletHolder(getBlockByLimitNextOnPBFTServlet), "/getblockbylimitnext");
    context
        .addServlet(new ServletHolder(getBlockByLatestNumOnPBFTServlet), "/getblockbylatestnum");
    context.addServlet(new ServletHolder(getMerkleTreeVoucherInfoOnPBFTServlet),
        "/getmerkletreevoucherinfo");
    context.addServlet(new ServletHolder(scanAndMarkNoteByIvkOnPBFTServlet),
        "/scanandmarknotebyivk");
    context.addServlet(new ServletHolder(scanNoteByIvkOnPBFTServlet), "/scannotebyivk");
    context.addServlet(new ServletHolder(scanNoteByOvkOnPBFTServlet), "/scannotebyovk");
    context.addServlet(new ServletHolder(isSpendOnPBFTServlet), "/isspend");
    context.addServlet(new ServletHolder(triggerConstantContractOnPBFTServlet),
        "/triggerconstantcontract");
    context.addServlet(new ServletHolder(estimateEnergyOnPBFTServlet), "/estimateenergy");

    // only for PBFTNode
    context.addServlet(new ServletHolder(getTransactionByIdOnPBFTServlet), "/gettransactionbyid");
    context.addServlet(new ServletHolder(getTransactionInfoByIdOnPBFTServlet),
        "/gettransactioninfobyid");

    context.addServlet(new ServletHolder(getTransactionCountByBlockNumOnPBFTServlet),
        "/gettransactioncountbyblocknum");

    context.addServlet(new ServletHolder(getNodeInfoOnPBFTServlet), "/getnodeinfo");
    context.addServlet(new ServletHolder(getBrokerageServlet), "/getBrokerage");
    context.addServlet(new ServletHolder(getRewardServlet), "/getReward");

    context.addServlet(new ServletHolder(getMarketOrderByAccountOnPBFTServlet),
        "/getmarketorderbyaccount");
    context.addServlet(new ServletHolder(getMarketOrderByIdOnPBFTServlet),
        "/getmarketorderbyid");
    context.addServlet(new ServletHolder(getMarketPriceByPairOnPBFTServlet),
        "/getmarketpricebypair");
    context.addServlet(new ServletHolder(getMarketOrderListByPairOnPBFTServlet),
        "/getmarketorderlistbypair");
    context.addServlet(new ServletHolder(getMarketPairListOnPBFTServlet),
        "/getmarketpairlist");

    context.addServlet(new ServletHolder(scanShieldedTRC20NotesByIvkOnPBFTServlet),
        "/scanshieldedtrc20notesbyivk");
    context.addServlet(new ServletHolder(scanShieldedTRC20NotesByOvkOnPBFTServlet),
        "/scanshieldedtrc20notesbyovk");
    context.addServlet(new ServletHolder(isShieldedTRC20ContractNoteSpentOnPBFTServlet),
        "/isshieldedtrc20contractnotespent");
    context.addServlet(new ServletHolder(getBurnTrxOnPBFTServlet),
        "/getburntrx");
    context.addServlet(new ServletHolder(getBandwidthPricesOnPBFTServlet),
        "/getbandwidthprices");
    context.addServlet(new ServletHolder(getEnergyPricesOnPBFTServlet),
        "/getenergyprices");
    context.addServlet(new ServletHolder(getBlockOnPBFTServlet),
        "/getblock");
```
