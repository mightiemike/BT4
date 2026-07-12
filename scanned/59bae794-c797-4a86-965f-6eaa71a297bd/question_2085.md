# Q2085: IssueDenom owner binding against denom owner

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then issue a denom then use authz or feegrant to mint from that denom, causing `denom owner` to diverge so the invariant `denom id is unique and cannot alias IBC voucher class ids` fails and the attacker can poison class trace handling and later unescrow a different NFT, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: poison class trace handling and later unescrow a different NFT by testing the owner binding angle against `denom owner` during `issue a denom then use authz or feegrant to mint from that denom`, with specific focus on denom owner authority.
- Invariant to test: denom id is unique and cannot alias IBC voucher class ids; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
