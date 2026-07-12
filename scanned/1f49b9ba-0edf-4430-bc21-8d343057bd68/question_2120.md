# Q2120: IssueDenom owner binding against Denom

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on collection membership, under the precondition that a valuable denom or NFT exists under the NFT module, then issue a denom then use authz or feegrant to mint from that denom, causing `Denom` to diverge so the invariant `native NFT denom state remains separate from IBC class trace state` fails and the attacker can poison class trace handling and later unescrow a different NFT, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: poison class trace handling and later unescrow a different NFT by testing the owner binding angle against `Denom` during `issue a denom then use authz or feegrant to mint from that denom`, with specific focus on collection membership.
- Invariant to test: native NFT denom state remains separate from IBC class trace state; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
