### Title
Missing Scoped `TransferAuthorization` Allows Authz Grantee to Transfer All Granter NFTs — (`x/nft-transfer/keeper/packet.go`, `x/nft-transfer/types/codec.go`)

---

### Summary

The `nft-transfer` module has no custom `Authorization` type for `MsgTransfer`. Any authz grant for `MsgTransfer` is therefore a blanket `GenericAuthorization`, allowing the grantee to transfer every NFT owned by the granter with no per-class or per-token-ID restriction.

---

### Finding Description

**Call path:**

```
grantee signs MsgExec{msgs: [MsgTransfer{Sender: granter, TokenIds: [...]}]}
  → authz module verifies grant exists for MsgTransfer (granter → grantee)
  → Transfer() [msg_server.go:18] derives sender = AccAddressFromBech32(msg.Sender) = granter
  → SendTransfer() → createOutgoingPacket()
  → owner check [packet.go:102]: !sender.Equals(owner) → false (granter IS owner) → passes
  → NFT escrowed / burned
```

**Root cause — `GetSigners` returns `msg.Sender` (the granter):** [1](#0-0) 

The authz module accepts the inner message because the grantee holds a grant from the granter for `MsgTransfer`. The keeper then derives `sender` from `msg.Sender` (the granter), not from the actual transaction signer (the grantee).

**Owner check in `createOutgoingPacket` passes trivially:** [2](#0-1) 

Because `sender` == granter == NFT owner, the check always passes regardless of which token IDs the grantee chose to include.

**No custom `Authorization` type is registered for `MsgTransfer`:** [3](#0-2) 

Only `sdk.Msg` is registered — there is no `TransferAuthorization` equivalent (contrast with ibc-go's fungible token transfer module, which ships `TransferAuthorization` scoped by port, channel, and spend limit). The grep across the entire repo confirms zero authz-related code in the `nft-transfer` module.

---

### Impact Explanation

A grantee holding a single `GenericAuthorization` for `MsgTransfer` (granter → grantee) can:

- Transfer **any** NFT in **any** class owned by the granter to **any** receiver.
- The granter has no way to restrict the grant to specific token IDs or class IDs.

This is a direct, unauthorized NFT transfer: the granter's entire NFT portfolio is at risk from a single broad grant.

---

### Likelihood Explanation

`GenericAuthorization` for `MsgTransfer` is the only available grant type. Any user or protocol that delegates transfer rights (e.g., a marketplace contract, a relayer, a custodian) is forced to issue a blanket grant. The exploit requires only that the grantee exist and that the granter owns NFTs — both are normal operational conditions.

---

### Recommendation

Implement a `TransferAuthorization` type for the `nft-transfer` module, analogous to ibc-go's `TransferAuthorization`, that restricts grants by:

- `ClassId` (one or more allowed class IDs)
- `TokenIds` (optional allowlist of token IDs within each class)
- Port and channel pair

Register it via `RegisterInterfaces` in `codec.go` and implement the `authz.Authorization` interface (`MsgTypeURL`, `Accept`, `ValidateBasic`). The `Accept` method should decrement or remove the allowed token IDs on each successful execution, preventing reuse beyond the granted scope.

---

### Proof of Concept

```go
// 1. granter mints NFT tokenA and tokenB in classX
// 2. granter issues GenericAuthorization{MsgTypeURL: MsgTransfer} to grantee
// 3. grantee submits:
MsgExec{
  Grantee: grantee,
  Msgs: [MsgTransfer{
    Sender:    granter,       // granter's address
    ClassId:   "classX",
    TokenIds:  ["tokenA", "tokenB"],  // ALL granter's NFTs
    Receiver:  attacker,
    ...
  }],
}
// 4. createOutgoingPacket: sender=granter, owner=granter → check passes
// 5. Both NFTs escrowed and sent to attacker
// Assert: granter no longer owns tokenA or tokenB
```

### Citations

**File:** x/nft-transfer/types/msgs.go (L91-96)
```go
func (msg MsgTransfer) GetSigners() []sdk.AccAddress {
	signer, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{signer}
```

**File:** x/nft-transfer/keeper/packet.go (L101-104)
```go
		owner := nft.GetOwner()
		if !sender.Equals(owner) {
			return channeltypes.Packet{}, newsdkerrors.Wrap(sdkerrors.ErrUnauthorized, "not token owner")
		}
```

**File:** x/nft-transfer/types/codec.go (L29-32)
```go
func RegisterInterfaces(registry codectypes.InterfaceRegistry) {
	registry.RegisterImplementations((*sdk.Msg)(nil), &MsgTransfer{})

	msgservice.RegisterMsgServiceDesc(registry, &_Msg_serviceDesc)
```
