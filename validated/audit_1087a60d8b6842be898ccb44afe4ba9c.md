### Title
Unsynchronized `HashMap` (`cheatWitnessInfoMap`) mutated by the P2P block-handling thread while iterated by the public `getnodeinfo` API thread - ([File: framework/src/main/java/org/tron/core/services/WitnessProductBlockService.java])

### Summary
`WitnessProductBlockService.cheatWitnessInfoMap` is a plain (non-thread-safe) `java.util.HashMap`. It is written to by the network message-handling thread when a duplicate-block ("cheat witness") is detected, and it is iterated by the `getnodeinfo` HTTP/gRPC API handler, which any unprivileged caller can invoke at any time. This is structurally the same bug class as the reported `deposit.Service.failedBlocks` race: a plain map mutated by one goroutine/thread while another concurrently reads/iterates it, risking corruption or a fatal error (in Java, a `ConcurrentModificationException` or, due to `HashMap`'s internal resizing during `put`, a silently corrupted map / infinite loop, which is a well-known JDK `HashMap` concurrency hazard).

### Finding Description
`cheatWitnessInfoMap` is declared as a plain `HashMap` with no external synchronization: [1](#0-0) 

It is mutated (`containsKey`, `put`, `get(...).clear()...increment()`) inside `validWitnessProductTwoBlock`, which is called synchronously from `BlockMsgHandler.processBlock` — the thread that processes inbound blocks from network peers: [2](#0-1) [3](#0-2) 

The same map is exposed unguarded via `queryCheatWitnessInfo()`: [4](#0-3) 

`NodeInfoService.setCheatWitnessInfo` iterates the map's `entrySet()` directly, on whatever thread services the `getnodeinfo` API call (HTTP/gRPC handler thread, independent from the P2P processing thread): [5](#0-4) 

`getNodeInfo()` (which calls `setCheatWitnessInfo`) is a public, unauthenticated node-info endpoint reachable by any client, and it is annotated `@MetricTime`, i.e. hit repeatedly and directly: [6](#0-5) 

Because `HashMap.put` can trigger internal table resize/rehash while another thread concurrently calls `entrySet().iterator()`/`hasNext()`/`next()` on `queryCheatWitnessInfo()`'s returned map (the same backing instance, not a defensive copy), this is unsafe under Java's memory model, exactly analogous to the reported Go `map` race: a single `HashMap`, no `synchronized`/`ConcurrentHashMap`/copy-on-read protection, mutated by one thread while iterated by another triggered by (indirectly) attacker-controlled/public input (an unprivileged user hitting the node-info API while duplicate/forked/cheat blocks are being processed from the P2P network, which is itself attacker-influenceable timing).

### Impact Explanation
A concurrent `HashMap` structural modification during iteration can throw `ConcurrentModificationException` (crashing/interrupting the request thread) or, in the worst case (rare but documented for `HashMap`), corrupt the internal bucket linked list during resize such that a concurrent reader spins forever, consuming CPU indefinitely (a known historical Java `HashMap` infinite-loop-on-rehash bug). Because the API is invoked repeatedly by any caller and the write path (cheat-witness detection) is triggered whenever the node detects duplicate blocks from the P2P network (partly influenced by external actors), an attacker who can induce cheat-witness events (or simply times calls around normal duplicate/fork block reception) can repeatedly race this window. This can cause request-thread exceptions or, in pathological cases, thread hangs/CPU exhaustion on the node's HTTP/gRPC service thread — a denial-of-service style invalid-state/availability impact against the node, not a fund-safety bug.

### Likelihood Explanation
Likelihood is moderate: the write path only fires when the same block number is produced twice by the same witness (a specific, not-attacker-fully-controlled condition), and the read path requires a caller to poll `getnodeinfo` at the right moment. This makes exploitation timing-dependent and non-trivial to reliably trigger versus, e.g., a hot-path map used every block, but it is a real, reachable unprivileged-facing code path (`getnodeinfo` is unauthenticated) racing against P2P-triggered writes, matching the report's bug class precisely.

### Recommendation
Replace `cheatWitnessInfoMap` with a `ConcurrentHashMap`, or protect all reads/writes with a lock (`synchronized`), and have `queryCheatWitnessInfo()` return a defensive copy (e.g., `new HashMap<>(cheatWitnessInfoMap)`) taken under that lock before it is returned to callers, mirroring the `sync.RWMutex`/`sync.Map` fix applied upstream for the analogous `failedBlocks` issue.

### Proof of Concept
1. Node A repeatedly (or via crafted duplicate blocks from a witness key it controls) triggers `BlockMsgHandler.processBlock` → `witnessProductBlockService.validWitnessProductTwoBlock(block)`, causing rapid `put`/`get().clear()...increment()` calls on `cheatWitnessInfoMap`.
2. Concurrently, an external client repeatedly calls the node-info API (`getnodeinfo`), which invokes `NodeInfoService.setCheatWitnessInfo` → `witnessProductBlockService.queryCheatWitnessInfo().entrySet()` and iterates it.
3. Under sufficient concurrency (e.g., a JUnit test spinning one thread calling `validWitnessProductTwoBlock` in a loop while another thread concurrently iterates `queryCheatWitnessInfo()`), a `ConcurrentModificationException` or corrupted/looping `HashMap` state can be observed, analogous to the Go reproduction in the referenced report.

### Citations

**File:** framework/src/main/java/org/tron/core/services/WitnessProductBlockService.java (L23-23)
```java
  private Map<String, CheatWitnessInfo> cheatWitnessInfoMap = new HashMap<>();
```

**File:** framework/src/main/java/org/tron/core/services/WitnessProductBlockService.java (L25-45)
```java
  public void validWitnessProductTwoBlock(BlockCapsule block) {
    try {
      BlockCapsule blockCapsule = historyBlockCapsuleCache.getIfPresent(block.getNum());
      if (blockCapsule != null && Arrays.equals(blockCapsule.getWitnessAddress().toByteArray(),
          block.getWitnessAddress().toByteArray()) && !Arrays.equals(block.getBlockId().getBytes(),
          blockCapsule.getBlockId().getBytes())) {
        String key = ByteArray.toHexString(block.getWitnessAddress().toByteArray());
        if (!cheatWitnessInfoMap.containsKey(key)) {
          CheatWitnessInfo cheatWitnessInfo = new CheatWitnessInfo();
          cheatWitnessInfoMap.put(key, cheatWitnessInfo);
        }
        cheatWitnessInfoMap.get(key).clear().setTime(System.currentTimeMillis())
            .setLatestBlockNum(block.getNum()).add(block).add(blockCapsule).increment();
      } else {
        historyBlockCapsuleCache.put(block.getNum(), new BlockCapsule(block.getInstance()));
      }
    } catch (Exception e) {
      logger.error("valid witness same time product two block fail! blockNum: {}, blockHash: {}",
          block.getNum(), block.getBlockId().toString(), e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/WitnessProductBlockService.java (L47-49)
```java
  public Map<String, CheatWitnessInfo> queryCheatWitnessInfo() {
    return cheatWitnessInfoMap;
  }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/BlockMsgHandler.java (L153-156)
```java
    try {
      tronNetDelegate.processBlock(block, false);
      peer.setBlockRcvTime(System.currentTimeMillis());
      witnessProductBlockService.validWitnessProductTwoBlock(block);
```

**File:** framework/src/main/java/org/tron/core/services/NodeInfoService.java (L60-69)
```java
  @MetricTime
  public NodeInfo getNodeInfo() {
    NodeInfo nodeInfo = new NodeInfo();
    setConnectInfo(nodeInfo);
    setMachineInfo(nodeInfo);
    setConfigNodeInfo(nodeInfo);
    setBlockInfo(nodeInfo);
    setCheatWitnessInfo(nodeInfo);
    return nodeInfo;
  }
```

**File:** framework/src/main/java/org/tron/core/services/NodeInfoService.java (L203-208)
```java
  protected void setCheatWitnessInfo(NodeInfo nodeInfo) {
    for (Entry<String, CheatWitnessInfo> entry : witnessProductBlockService.queryCheatWitnessInfo()
        .entrySet()) {
      nodeInfo.getCheatWitnessInfoMap().put(entry.getKey(), entry.getValue().toString());
    }
  }
```
