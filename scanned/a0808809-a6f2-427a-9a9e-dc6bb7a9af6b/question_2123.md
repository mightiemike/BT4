# Q2123: IssueDenom owner binding against denom owner

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on collection membership, under the precondition that a valuable denom or NFT exists under the NFT module, then issue denom, transfer NFTs, and attempt IBC send through nft-transfer, causing `denom owner` to diverge so the invariant `address prefix validation binds the denom owner to the signer` fails and the attacker can create a denom aliasing conflict that lets attacker mint into another collection, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: create a denom aliasing conflict that lets attacker mint into another collection by testing the owner binding angle against `denom owner` during `issue denom, transfer NFTs, and attempt IBC send through nft-transfer`, with specific focus on collection membership.
- Invariant to test: address prefix validation binds the denom owner to the signer; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
