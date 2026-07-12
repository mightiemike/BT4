# Q2111: IssueDenom owner binding against collection index

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on token owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then issue multiple denoms with ambiguous normalized or hashed-looking representations, causing `collection index` to diverge so the invariant `only the denom owner can mint NFTs under that denom` fails and the attacker can create a denom aliasing conflict that lets attacker mint into another collection, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: create a denom aliasing conflict that lets attacker mint into another collection by testing the owner binding angle against `collection index` during `issue multiple denoms with ambiguous normalized or hashed-looking representations`, with specific focus on token owner authority.
- Invariant to test: only the denom owner can mint NFTs under that denom; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
