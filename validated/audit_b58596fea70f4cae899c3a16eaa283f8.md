### Title
NFT Denom Creation Can Be Frontrun, Permanently Blocking Legitimate Creators and Hijacking Denom Namespace - (File: x/nft/keeper/denom.go)

### Summary
The `x/nft` module requires users to supply a user-chosen `denom_id` and `denom_name` in `MsgIssueDenom`. Because both values must be globally unique and are checked only at execution time, any observer of a pending `MsgIssueDenom` transaction can submit an identical message first. The attacker's transaction succeeds, registering the attacker as the `creator` of the denom. The victim's transaction then reverts with `ErrInvalidDenom`. The attacker gains exclusive minting rights over the hijacked denom namespace.

### Finding Description
`MsgIssueDenom` accepts a user-supplied `id` and `name` field: [1](#0-0) 

The message server passes these directly to `Keeper.IssueDenom`, which calls `SetDenom`: [2](#0-1) 

`SetDenom` checks uniqueness of both `id` and `name` at execution time and reverts if either is already taken: [3](#0-2) 

There is no binding between the submitting address and the chosen `denom_id` before the transaction is committed. An attacker who observes a pending `MsgIssueDenom` in the mempool can resubmit the same `id` and `name` with a higher gas fee (or collude with a validator to reorder within a block). The attacker's transaction lands first, setting `denom.Creator` to the attacker's address. The victim's transaction then hits the `HasDenomID` guard and reverts.

The corrupted on-chain value is the `creator` field of the `Denom` struct. Because `MintNFT` enforces `IsDenomCreator` before allowing any minting: [4](#0-3) [5](#0-4) 

the attacker now holds exclusive minting rights over the hijacked denom. The legitimate creator is permanently locked out of that `denom_id` and `denom_name`.

### Impact Explanation
- The victim cannot issue their intended denom; the `denom_id` and `denom_name` are permanently claimed by the attacker.
- The attacker becomes the sole authorized minter for that denom, gaining control over the NFT collection namespace the victim intended to create.
- An attacker can repeat this for every `MsgIssueDenom` transaction visible in the mempool, systematically preventing any new NFT collection from being created by its intended owner.
- The corrupted invariant is: `Denom.Creator == msg.Sender` for the legitimate `MsgIssueDenom` submitter. After the attack, `Denom.Creator` is the attacker's address.

### Likelihood Explanation
- `MsgIssueDenom` is a standard, publicly documented CLI transaction (`chain-maind tx nft issue <denom-id>`). [6](#0-5) 

- The Cosmos SDK mempool is public; pending transactions are visible to all nodes before block inclusion.
- A validator or a user with validator connections can trivially reorder two transactions within the same block.
- No special privilege is required — any unprivileged account with enough funds to pay fees can execute this attack.

### Recommendation
Remove the user-chosen `denom_id` from `MsgIssueDenom`. Instead, derive the denom ID deterministically from the sender address and a per-sender counter (or a hash of `sender || sequence_number`). This makes the resulting ID unpredictable to observers and ties it to the sender, eliminating the frontrunning surface entirely.

### Proof of Concept
1. Alice broadcasts `MsgIssueDenom{Id: "myproject", Name: "My Project", Sender: alice}`.
2. Bob (attacker) observes the pending transaction in the mempool and broadcasts `MsgIssueDenom{Id: "myproject", Name: "My Project", Sender: bob}` with a higher gas fee.
3. Bob's transaction is included first. `SetDenom` stores `Denom{Id: "myproject", Creator: bob}`.
4. Alice's transaction executes next. `HasDenomID("myproject")` returns `true`; `SetDenom` returns `ErrInvalidDenom: denomID myproject has already exists`. Alice's transaction reverts.
5. Bob is now the exclusive creator of `"myproject"` and can mint arbitrary NFTs under it. Alice can never reclaim that denom ID.
6. Bob repeats for every `MsgIssueDenom` he observes, blocking all new NFT collection creation. [7](#0-6)

### Citations

**File:** proto/chainmain/nft/v1/tx.proto (L32-41)
```text
message MsgIssueDenom {
  option (cosmos.msg.v1.signer) = "sender";
  option (gogoproto.equal)      = true;

  string id     = 1;
  string name   = 2;
  string schema = 3;
  string sender = 4;
  string uri    = 5;
}
```

**File:** x/nft/keeper/msg_server.go (L25-34)
```go
func (m msgServer) IssueDenom(goCtx context.Context, msg *types.MsgIssueDenom) (*types.MsgIssueDenomResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
		return nil, err
	}
```

**File:** x/nft/keeper/denom.go (L26-39)
```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
	if k.HasDenomID(ctx, denom.Id) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
	}

	if k.HasDenomNm(ctx, denom.Name) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
	}

	store := ctx.KVStore(k.storeKey)
	bz := k.cdc.MustMarshal(&denom)
	store.Set(types.KeyDenomID(denom.Id), bz)
	store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
	return nil
```

**File:** x/nft/keeper/denom.go (L79-97)
```go
// IsDenomCreator checks if address is the creator of Denom
// Return the Denom if true, an error otherwise
func (k Keeper) IsDenomCreator(ctx sdk.Context, denomID string, address sdk.AccAddress) (types.Denom, error) {
	denom, err := k.GetDenom(ctx, denomID)
	if err != nil {
		return types.Denom{}, err
	}

	creator, err := sdk.AccAddressFromBech32(denom.Creator)
	if err != nil {
		panic(err)
	}

	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
	}

	return denom, nil
}
```

**File:** x/nft/keeper/keeper.go (L72-82)
```go
func (k Keeper) MintNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, sender, owner sdk.AccAddress,
) error {
	_, err := k.IsDenomCreator(ctx, denomID, sender)
	if err != nil {
		return err
	}

	return k.MintNFTUnverified(ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, owner)
}
```

**File:** x/nft/client/cli/tx.go (L42-53)
```go
func GetCmdIssueDenom() *cobra.Command {
	cmd := &cobra.Command{
		Use:  "issue [denom-id]",
		Long: "Issue a new denom.",
		Example: fmt.Sprintf(`$ %s tx nft issue <denom-id>
  --from=<key-name>
  --name=<denom-name>
  --schema=<schema-content or path to schema.json>
  --uri=<uri of denom>
  --chain-id=<chain-id>
  --fees=<fee>`, version.AppName),
		Args: cobra.ExactArgs(1),
```
