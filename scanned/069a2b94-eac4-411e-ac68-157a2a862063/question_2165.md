# Q2165: IssueDenom owner binding against denom owner

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then issue a denom then use authz or feegrant to mint from that denom, causing `denom owner` to diverge so the invariant `address prefix validation binds the denom owner to the signer` fails and the attacker can create a denom aliasing conflict that lets attacker mint into another collection, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: create a denom aliasing conflict that lets attacker mint into another collection by testing the owner binding angle against `denom owner` during `issue a denom then use authz or feegrant to mint from that denom`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: address prefix validation binds the denom owner to the signer; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
