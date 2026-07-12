# Q2112: IssueDenom owner binding against nft module store

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on token owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then issue a denom whose id resembles an IBC voucher class id and then mint, causing `nft module store` to diverge so the invariant `denom metadata cannot corrupt collection or class-trace interpretation` fails and the attacker can create a denom aliasing conflict that lets attacker mint into another collection, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: create a denom aliasing conflict that lets attacker mint into another collection by testing the owner binding angle against `nft module store` during `issue a denom whose id resembles an IBC voucher class id and then mint`, with specific focus on token owner authority.
- Invariant to test: denom metadata cannot corrupt collection or class-trace interpretation; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
