### Title
Empty-signature PBFT commit messages bypass all signature validation in `PbftDataSyncHandler.validPbftSign` - ([File: framework/src/main/java/org/tron/core/net/messagehandler/PbftDataSyncHandler.java])

### Summary
`validPbftSign` only performs signature verification when `srSignList.size() != 0`, so a `PbftCommitMessage` carrying an empty `signature` list bypasses the quorum-count check, the SR-address check, and the full-consumption check entirely, returning `true` unconditionally. [1](#0-0)  This lets an attacker-controlled `Raw` payload with arbitrary `dataType`/`epoch`/`viewN`/`data` be persisted as if it were a validly-signed PBFT commit record.

### Finding Description
`processMessage` accepts any `PbftCommitMessage` from a connected peer (reached via `PBFT_COMMIT_MSG` in `P2pEventHandlerImpl.processMessage`), parses the attacker-supplied bytes into a `Raw` object, and caches it keyed by `raw.getViewN()` without any validation at this stage. [2](#0-1)  Later, `processPBFTCommitData`/`processPBFTCommitMessage` pulls this cached message and calls `validPbftSign(raw, pbftCommitMessage.getPBFTCommitResult().getSignatureList(), chainBaseManager.getWitnesses())`. [3](#0-2) 

The vulnerable check is:
```java
if (srSignList.size() != 0) {
  ... quorum count check, per-signature ECDSA recovery+address check, full-consumption check ...
}
return true;
``` [4](#0-3)  When `srSignList` is empty, the entire `if` block — including the `Param.getInstance().getAgreeNodeCount()` quorum requirement and the per-signer `ECKey.signatureToAddress` verification against the current SR set — is skipped, and the function returns `true` with zero checks performed.

If `validPbftSign` returns `true`, `processPBFTCommitMessage` persists the message: `pbftSignDataStore.putBlockSignData(raw.getViewN(), ...)` for `DataType.BLOCK` or `pbftSignDataStore.putSrSignData(raw.getEpoch(), ...)` for `DataType.SRL`, gated only by a "no existing entry" check (`getBlockSignData(...) == null` / `getSrSignData(...) == null`), i.e. only the first commit for a given key is stored — an attacker racing ahead of the real quorum result can permanently occupy that slot with unsigned garbage. [5](#0-4) 

Once stored, this forged, unsigned commit is re-served to other peers: `FetchInvDataMsgHandler.sendPbftCommitMessage` reads it back via `tronNetDelegate.getBlockPbftCommitData`/`getSRLPbftCommitData` and forwards it unconditionally as a new `PbftCommitMessage` to any peer that fetches the corresponding block/epoch data, without re-validating signatures on the send side. [6](#0-5) 

The reachable path is fully unprivileged: any connected P2P peer can send a crafted `PbftCommitMessage` over the wire; there is no signature, authentication, or peer-trust gating in `P2pEventHandlerImpl.onMessage`/`processMessage` before dispatch to `pbftDataSyncHandler.processMessage`. [7](#0-6) 

### Impact Explanation
This corrupts the persisted PBFT finality/checkpoint record store (`PbftSignDataStore`) with attacker-forged, unsigned data for a given `viewN` (block) or `epoch` (SRL), because the store's "write-once" guard (`getBlockSignData(...) == null`) means the first writer — potentially the attacker — wins permanently for that key, preventing the legitimate quorum-signed commit from ever being recorded. Any downstream logic relying on `PbftSignDataStore` (finality checkpoints, SRL commit propagation, and any consumer trusting `getBlockPbftCommitData`/`getSRLPbftCommitData` as an authenticated 2/3+1 SR attestation) would treat this forged, zero-signature record as a settled finality result, and it is further propagated to other peers via `sendPbftCommitMessage`, spreading the corruption across the network.

### Likelihood Explanation
The attack requires only an unprivileged P2P connection to a node with `allowPBFT()` enabled — no special keys, no governance access, no local file/storage access. The attacker must win a race against the legitimate signers to have their empty-signature message cached/processed for a given `viewN`/`epoch` before the real quorum commit is persisted (due to the null-check gating in `putBlockSignData`/`putSrSignData`), which is feasible on an open, unsynced peer or shortly after `allowPBFT()` activates for a given block/epoch. This is repeatable per unpersisted `viewN`/`epoch` slot.

### Recommendation
Remove the short-circuit: `validPbftSign` should reject (return `false`) whenever `srSignList` does not already meet the quorum requirement (`srSignSet.size() >= Param.getInstance().getAgreeNodeCount()`), regardless of whether the list is empty, instead of only checking non-emptiness. Additionally, consider validating `raw.getEpoch()`/`raw.getViewN()`/`raw.getDataType()` bounds before caching in `processMessage`, and re-validating signatures again before re-serving stored `PbftSignCapsule` data in `FetchInvDataMsgHandler.sendPbftCommitMessage`.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/net/messagehandler/PbftDataSyncHandlerTest.java
@Test
public void testEmptySignatureListBypassesValidation() throws Exception {
  PbftDataSyncHandler handler = new PbftDataSyncHandler();

  // Attacker-forged Raw payload with arbitrary viewN/epoch/dataType
  Protocol.PBFTMessage.Raw raw = Protocol.PBFTMessage.Raw.newBuilder()
      .setViewN(999L)
      .setEpoch(12345L)
      .setDataType(Protocol.PBFTMessage.DataType.BLOCK)
      .setMsgType(Protocol.PBFTMessage.MsgType.COMMIT)
      .setData(ByteString.copyFromUtf8("forged"))
      .build();

  // empty signature list -- no valid SR signatures
  PbftSignCapsule forgedCapsule = new PbftSignCapsule(raw.toByteString(), new ArrayList<>());
  PbftCommitMessage forgedMsg = new PbftCommitMessage(forgedCapsule);

  ChainBaseManager chainBaseManager = Mockito.mock(ChainBaseManager.class);
  DynamicPropertiesStore dps = Mockito.mock(DynamicPropertiesStore.class);
  PbftSignDataStore signStore = Mockito.mock(PbftSignDataStore.class);
  Mockito.when(chainBaseManager.getDynamicPropertiesStore()).thenReturn(dps);
  Mockito.when(dps.allowPBFT()).thenReturn(true);
  Mockito.when(chainBaseManager.getPbftSignDataStore()).thenReturn(signStore);
  Mockito.when(signStore.getBlockSignData(999L)).thenReturn(null);
  // no witnesses configured -- attacker controls no keys
  Mockito.when(chainBaseManager.getWitnesses()).thenReturn(new ArrayList<>());

  Field field = PbftDataSyncHandler.class.getDeclaredField("chainBaseManager");
  field.setAccessible(true);
  field.set(handler, chainBaseManager);

  // invoke private processPBFTCommitMessage via reflection
  Method m = PbftDataSyncHandler.class.getDeclaredMethod(
      "processPBFTCommitMessage", PbftCommitMessage.class);
  m.setAccessible(true);
  m.invoke(handler, forgedMsg);

  // BUG: forged, zero-signature commit gets persisted despite no valid SR signatures
  Mockito.verify(signStore, Mockito.times(1))
      .putBlockSignData(999L, forgedCapsule);
}
```
Expected (buggy) result: `putBlockSignData` is invoked with the forged capsule despite `signatureList` being empty and no witnesses signing anything, confirming `validPbftSign` returns `true` for an empty signature list and the unauthenticated record is persisted (and subsequently re-servable via `FetchInvDataMsgHandler.sendPbftCommitMessage`).

### Citations

**File:** framework/src/main/java/org/tron/core/net/messagehandler/PbftDataSyncHandler.java (L53-65)
```java
  @Override
  public void processMessage(PeerConnection peer, TronMessage msg) throws P2pException {
    PbftCommitMessage pbftCommitMessage = (PbftCommitMessage) msg;
    try {
      if (!chainBaseManager.getDynamicPropertiesStore().allowPBFT()) {
        return;
      }
      Raw raw = Raw.parseFrom(pbftCommitMessage.getPBFTCommitResult().getData());
      pbftCommitMessageCache.put(raw.getViewN(), pbftCommitMessage);
    } catch (InvalidProtocolBufferException e) {
      logger.error("", e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/PbftDataSyncHandler.java (L100-120)
```java
  private void processPBFTCommitMessage(PbftCommitMessage pbftCommitMessage) {
    try {
      PbftSignDataStore pbftSignDataStore = chainBaseManager.getPbftSignDataStore();
      Raw raw = Raw.parseFrom(pbftCommitMessage.getPBFTCommitResult().getData());
      if (!validPbftSign(raw, pbftCommitMessage.getPBFTCommitResult().getSignatureList(),
          chainBaseManager.getWitnesses())) {
        return;
      }
      if (raw.getDataType() == DataType.BLOCK
          && pbftSignDataStore.getBlockSignData(raw.getViewN()) == null) {
        pbftSignDataStore.putBlockSignData(raw.getViewN(), pbftCommitMessage.getPbftSignCapsule());
        logger.info("Save the block {} pbft commit data", raw.getViewN());
      } else if (raw.getDataType() == DataType.SRL
          && pbftSignDataStore.getSrSignData(raw.getEpoch()) == null) {
        pbftSignDataStore.putSrSignData(raw.getEpoch(), pbftCommitMessage.getPbftSignCapsule());
        logger.info("Save the srl {} pbft commit data", raw.getEpoch());
      }
    } catch (InvalidProtocolBufferException e) {
      logger.error("", e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/PbftDataSyncHandler.java (L122-154)
```java
  private boolean validPbftSign(Raw raw, List<ByteString> srSignList,
      List<ByteString> currentSrList) {
    //valid sr list
    if (srSignList.size() != 0) {
      Set<ByteString> srSignSet = new ConcurrentSet();
      srSignSet.addAll(srSignList);
      if (srSignSet.size() < Param.getInstance().getAgreeNodeCount()) {
        logger.error("sr sign count {} < sr count * 2/3 + 1 == {}", srSignSet.size(),
            Param.getInstance().getAgreeNodeCount());
        return false;
      }
      byte[] dataHash = Sha256Hash.hash(true, raw.toByteArray());
      Set<ByteString> srSet = Sets.newHashSet(currentSrList);
      List<Future<Boolean>> futureList = new ArrayList<>();
      for (ByteString sign : srSignList) {
        futureList.add(executorService.submit(
            new ValidPbftSignTask(raw.getViewN(), srSignSet, dataHash, srSet, sign)));
      }
      for (Future<Boolean> future : futureList) {
        try {
          if (!future.get()) {
            return false;
          }
        } catch (Exception e) {
          logger.error("", e);
        }
      }
      if (srSignSet.size() != 0) {
        return false;
      }
    }
    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/net/messagehandler/FetchInvDataMsgHandler.java (L112-140)
```java
  private void sendPbftCommitMessage(PeerConnection peer, BlockCapsule blockCapsule) {
    try {
      if (!tronNetDelegate.allowPBFT() || peer.isSyncFinish()) {
        return;
      }
      long epoch = 0;
      PbftSignCapsule pbftSignCapsule = tronNetDelegate
              .getBlockPbftCommitData(blockCapsule.getNum());
      long maintenanceTimeInterval = consensusDelegate.getDynamicPropertiesStore()
              .getMaintenanceTimeInterval();
      if (pbftSignCapsule != null) {
        Raw raw = Raw.parseFrom(pbftSignCapsule.getPbftCommitResult().getData());
        epoch = raw.getEpoch();
        peer.sendMessage(new PbftCommitMessage(pbftSignCapsule));
      } else {
        epoch = (blockCapsule.getTimeStamp() / maintenanceTimeInterval + 1)
                * maintenanceTimeInterval;
      }
      if (epochCache.getIfPresent(epoch) == null) {
        PbftSignCapsule srl = tronNetDelegate.getSRLPbftCommitData(epoch);
        if (srl != null) {
          epochCache.put(epoch, true);
          peer.sendMessage(new PbftCommitMessage(srl));
        }
      }
    } catch (Exception e) {
      logger.error("", e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/net/P2pEventHandlerImpl.java (L122-198)
```java
  @Override
  public void onMessage(Channel c, byte[] data) {
    PeerConnection peerConnection = PeerManager.getPeerConnection(c);
    if (peerConnection == null) {
      logger.warn("Receive msg from unknown peer {}", c.getInetSocketAddress());
      return;
    }

    if (MessageTypes.PBFT_MSG.asByte() == data[0]) {
      PbftMessage message = null;
      try {
        message = (PbftMessage) PbftMessageFactory.create(data);
        pbftMsgHandler.processMessage(peerConnection, message);
      } catch (Exception e) {
        logger.warn("PBFT Message from {} process failed, {}",
                peerConnection.getInetSocketAddress(), message, e.getMessage());
        peerConnection.disconnect(Protocol.ReasonCode.BAD_PROTOCOL);
      }
      return;
    }

    processMessage(peerConnection, data);
  }

  private void processMessage(PeerConnection peer, byte[] data) {
    long startTime = System.currentTimeMillis();
    TronMessage msg = null;
    MessageTypes type = null;
    try {
      msg = TronMessageFactory.create(data);
      type = msg.getType();

      if (INVENTORY.equals(type) && !checkInvRateLimit(peer, (InventoryMessage) msg)) {
        return;
      }

      peer.getPeerStatistics().messageStatistics.addTcpInMessage(msg);
      if (PeerConnection.needToLog(msg)) {
        logger.info("Receive message from  peer: {}, {}", peer.getInetSocketAddress(), msg);
      }

      switch (type) {
        case P2P_PING:
        case P2P_PONG:
          keepAliveService.processMessage(peer, msg);
          break;
        case P2P_HELLO:
          handshakeService.processHelloMessage(peer, (HelloMessage) msg);
          break;
        case P2P_DISCONNECT:
          if (peer.getP2pRateLimiter().tryAcquire(type.asByte())) {
            peer.getChannel().close();
            peer.getNodeStatistics()
                .nodeDisconnectedRemote(((DisconnectMessage)msg).getReason());
          }
          break;
        case SYNC_BLOCK_CHAIN:
          syncBlockChainMsgHandler.processMessage(peer, msg);
          break;
        case BLOCK_CHAIN_INVENTORY:
          chainInventoryMsgHandler.processMessage(peer, msg);
          break;
        case INVENTORY:
          inventoryMsgHandler.processMessage(peer, msg);
          break;
        case FETCH_INV_DATA:
          fetchInvDataMsgHandler.processMessage(peer, msg);
          break;
        case BLOCK:
          blockMsgHandler.processMessage(peer, msg);
          break;
        case TRXS:
          transactionsMsgHandler.processMessage(peer, msg);
          break;
        case PBFT_COMMIT_MSG:
          pbftDataSyncHandler.processMessage(peer, msg);
          break;
```
