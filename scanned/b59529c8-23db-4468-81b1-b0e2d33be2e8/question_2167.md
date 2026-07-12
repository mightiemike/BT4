# Q2167: IssueDenom owner binding against collection index

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then issue a denom whose id resembles an IBC voucher class id and then mint, causing `collection index` to diverge so the invariant `denom id is unique and cannot alias IBC voucher class ids` fails and the attacker can make downstream NFT transfer treat attacker-issued denom as backed by escrow, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: make downstream NFT transfer treat attacker-issued denom as backed by escrow by testing the owner binding angle against `collection index` during `issue a denom whose id resembles an IBC voucher class id and then mint`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: denom id is unique and cannot alias IBC voucher class ids; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
