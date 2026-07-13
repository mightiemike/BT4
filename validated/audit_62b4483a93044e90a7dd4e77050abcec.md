### Title
`MsgIssueDenom` Denom ID Has No Sender Binding, Enabling Front-Running to Steal NFT Collection Creator Role - (File: `x/nft/keeper/denom.go`)

### Summary

`SetDenom` checks only whether a denom ID already exists, with no binding to the transaction sender. Any unprivileged user who observes a pending `MsgIssueDenom` in the mempool can rebroadcast the same `id` and `name` with a higher gas price, land first in the block, and become the denom creator. The legitimate sender's transaction then reverts with `"denomID X has already exists"`, and the attacker permanently holds the creator role for that collection.

### Finding Description

`SetDenom` in `x/nft/keeper/denom.go` enforces uniqueness of `denom.Id` and `denom.Name` globally, but the check is purely existence-based and carries no binding to `msg.Sender`:

```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
    if k.HasDenomID(ctx, denom.Id) {
        return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
    }
    if k.HasDenomNm(ctx, denom.Name) {
        return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
    }
    ...
    store.Set(types.KeyDenomID(denom.Id), bz)   // creator = whoever lands first
    store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
    return nil
}
``` [1](#0-0) 

The `IssueDenom` keeper method passes the `creator` address only as data to be stored, not as a uniqueness discriminator: [2](#0-1) 

The message server extracts `msg.Sender` and forwards it as `creator`, but the collision check fires before the creator is ever compared: [3](#0-2) 

`MintNFT` enforces that only the denom creator can mint tokens under a denom: [4](#0-3) 

So whoever wins the race to register a denom ID gains exclusive minting rights over that collection forever.

### Impact Explanation

The corrupted state value is the **denom creator field** stored in `KeyDenomID`. The attacker becomes the authoritative creator of the collection, which grants:

1. **Exclusive minting rights** — only the creator can call `MintNFT` for that denom. The attacker can mint arbitrary NFTs under the victim's intended collection identity.
2. **IBC NFT transfer abuse** — the `x/nft-transfer` module uses `denom_id` as the `base_class_id` for ICS-721 packets. An attacker who controls the denom can mint counterfeit NFTs and relay them cross-chain, corrupting the class trace on receiving chains.
3. **Permanent denial of the legitimate creator** — the victim can never reclaim the denom ID; the revert is final. [5](#0-4) [6](#0-5) 

### Likelihood Explanation

Cosmos SDK mempools are public. Any node operator or RPC observer can watch for pending `MsgIssueDenom` transactions. Transaction ordering within a block is determined by the proposer, who by default orders by gas price. An attacker needs only to:

1. Subscribe to mempool events or poll the RPC.
2. Rebroadcast the same `id` and `name` with a marginally higher gas price.
3. Wait one block.

No privileged access, leaked keys, or social engineering is required. The CLI entry point is fully open: [7](#0-6) 

### Recommendation

Bind the denom ID to the creator address at registration time. Two approaches:

1. **Derive the denom ID from the sender**: compute `id = hash(sender || user_chosen_suffix)` so no two senders can produce the same ID.
2. **Include sender in the uniqueness key**: store denoms under a composite key `(creator, id)` rather than a global `id` key, and require the same sender to reclaim or update.

Either approach mirrors the fix applied to the SEDA protocol (adding `msg.sender` to the request ID derivation) and eliminates the front-running surface entirely.

### Proof of Concept

1. Victim broadcasts:
   ```
   MsgIssueDenom { id: "blue-chip-nft", name: "Blue Chip NFT", sender: victim }
   ```
2. Attacker observes the mempool and broadcasts with higher gas:
   ```
   MsgIssueDenom { id: "blue-chip-nft", name: "Blue Chip NFT", sender: attacker }
   ```
3. Attacker's tx lands first. `SetDenom` stores `creator = attacker`.
4. Victim's tx executes `HasDenomID("blue-chip-nft") == true` → reverts with `ErrInvalidDenom`.
5. Attacker calls `MintNFT` for `denom_id = "blue-chip-nft"` — succeeds because `IsDenomCreator` returns `attacker`.
6. Attacker transfers minted NFTs cross-chain via `x/nft-transfer`, embedding the stolen denom ID in ICS-721 packets. [1](#0-0) [4](#0-3)

### Citations

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

**File:** x/nft/keeper/keeper.go (L36-42)
```go
// IssueDenom issues a denom according to the given params
func (k Keeper) IssueDenom(ctx sdk.Context,
	id, name, schema, uri string,
	creator sdk.AccAddress,
) error {
	return k.SetDenom(ctx, types.NewDenom(id, name, schema, uri, creator))
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

**File:** x/nft/types/errors.go (L16-16)
```go
	ErrInvalidDenom      = sdkerrors.Register(ModuleNameAlias, 9, "invalid denom")
```

**File:** x/nft-transfer/types/trace.go (L92-107)
```go
// Hash returns the hex bytes of the SHA256 hash of the ClassTrace fields using the following formula:
//
// hash = sha256(tracePath + "/" + baseClassId)
func (ct ClassTrace) Hash() tmbytes.HexBytes {
	hash := sha256.Sum256([]byte(ct.GetFullClassPath()))
	return hash[:]
}

// IBCClassID a classID for an ICS721 non-fungible token in the format
// 'ibc/{hash(tracePath + BaseClassId)}'. If the trace is empty, it will return the base classID.
func (ct ClassTrace) IBCClassID() string {
	if ct.Path != "" {
		return fmt.Sprintf("%s/%s", ClassPrefix, ct.Hash())
	}
	return ct.BaseClassId
}
```

**File:** x/nft/client/cli/tx.go (L41-88)
```go
// GetCmdIssueDenom is the CLI command for an IssueDenom transaction
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
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			denomName, err := cmd.Flags().GetString(FlagDenomName)
			if err != nil {
				return err
			}
			schema, err := cmd.Flags().GetString(FlagSchema)
			if err != nil {
				return err
			}
			uri, err := cmd.Flags().GetString(FlagDenomURI)
			if err != nil {
				return err
			}
			optionsContent, err := os.ReadFile(filepath.Clean(schema))
			if err == nil {
				schema = string(optionsContent)
			}

			msg := types.NewMsgIssueDenom(
				args[0],
				denomName,
				schema,
				uri,
				clientCtx.GetFromAddress().String(),
			)
			if err := msg.ValidateBasic(); err != nil {
				return err
			}
			return tx.GenerateOrBroadcastTxCLI(clientCtx, cmd.Flags(), msg)
		},
```
