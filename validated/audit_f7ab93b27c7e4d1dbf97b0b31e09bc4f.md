### Title
Cross-origin password credential disclosure via unvalidated `domain` parameter in `RemoteActorCredentialSharingImpl::RequestAgentAuthentication` - (File: chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc)

### Summary
`chrome.requestAgentAuthentication(gaia_id, domain, task_id, callback)` is exposed only to a small allow-list of origins (`gemini.google.com` and related staging hosts) via `IsRemoteActorCredentialSharingAllowedForOrigin`, checked in `BindReceiver`. However, once bound, the Mojo handler never validates that the caller-supplied `domain` argument has any relationship to the calling frame's own origin. The `domain` string is used verbatim to query the password store and to construct `params.web_origin` for `RemoteActorCredentialSharingService::SharePassword`. This lets any script running on the allowed origin request — and, if the user confirms the resulting picker dialog — exfiltrate saved credentials for an arbitrary third-party domain (e.g. a bank) to a remote actor task, not just credentials belonging to the caller's own origin.

### Finding Description
`BindReceiver` gates *interface access* to specific origins: [1](#0-0) 

But `ValidateRequestPreconditions`, the only per-call validation routine, checks frame type, user activation, process/origin lock consistency, and argument length — it never compares `domain` to `target_frame.GetLastCommittedOrigin()`: [2](#0-1) 

The unchecked `domain` is then used directly to build a `PasswordFormDigest` and query the profile/account password stores for logins on that domain: [3](#0-2) 

and, after the user approves a credential in the selection dialog, the same attacker-controlled `domain` becomes the `web_origin` shared to the remote actor sharing service: [4](#0-3) 

This mirrors the analog bug class from the report: a security check that is supposed to bind two related identifiers together (there, `oldProposalID` vs `newProposalID`; here, the requesting page's origin vs. the `domain` whose credentials are being requested) is missing, so an operation that should only be allowed "for self" is allowed "for anyone."

### Impact Explanation
Any of the small number of allow-listed origins (which run interactive web app code, e.g. `gemini.google.com`) can request saved passwords for **any** domain the user has stored credentials for, not just its own origin, and have them shared out via the remote actor sharing pipeline. This breaks the origin-scoping assumption underpinning the feature — it should only ever operate on credentials relevant to the requesting site/context — and constitutes a cross-origin disclosure of sensitive credential material. The confirmation dialog is the only remaining mitigation, and it is a UI-based control rather than a structural origin check.

### Likelihood Explanation
No special privileges, extra flags, or MITM are required — a script running in the top-level document of an allowed origin (or XSS on that origin) can call the already-exposed `chrome.requestAgentAuthentication` JS API with an arbitrary `domain` string, satisfying all checks in `ValidateRequestPreconditions` (main frame, user gesture, matching process/origin lock — all about the *caller*, not the *target domain*). The only remaining barrier is user interaction with the credential-picker dialog.

### Recommendation
In `ValidateRequestPreconditions` (or before `QueryPasswordStores`), verify that the requested `domain` is legitimately associated with the calling context (e.g., restrict to the calling frame's own eTLD+1/origin, or otherwise cryptographically bind the requested domain to an explicit, verifiable relationship such as a signed task descriptor) instead of trusting the renderer-supplied string outright.

### Proof of Concept
1. From a page on `https://gemini.google.com` (or any origin in the allow-list / `kRemoteActorCredentialSharingAllowedHostForTesting`), with a user gesture, call:
```js
chrome.requestAgentAuthentication('<victim_gaia_id>', 'unrelated-bank.com', 'task_id_x', (success) => { ... });
```
2. `RequestAgentAuthentication` passes `ValidateRequestPreconditions` (all checks concern the caller's frame/process, not `domain`) at [2](#0-1) .
3. `QueryPasswordStores` fetches the user's stored logins for `unrelated-bank.com` at [5](#0-4) .
4. If the user approves the resulting picker dialog, `ProceedWithCredential` shares the `unrelated-bank.com` credential to the remote actor service with `params.web_origin` derived from the attacker-supplied `domain`, at [6](#0-5) .

This confirms the missing "domain-belongs-to-caller" check, allowing any allow-listed origin to request cross-origin credential disclosure — analogous to the missing self-referential-ID check in the referenced smart-contract finding.

### Citations

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L74-91)
```text
void RemoteActorCredentialSharingImpl::BindReceiver(
    mojo::PendingAssociatedReceiver<chrome::mojom::RemoteActorCredentialSharing>
        receiver,
    content::RenderFrameHost* rfh) {
  if (!rfh->IsInPrimaryMainFrame()) {
    return;
  }
  if (!IsRemoteActorCredentialSharingAllowedForOrigin(
          rfh->GetLastCommittedOrigin())) {
    rfh->GetProcess()->ShutdownForBadMessage(
        content::RenderProcessHost::CrashReportMode::GENERATE_CRASH_DUMP);
    return;
  }

  auto* impl =
      RemoteActorCredentialSharingImpl::GetOrCreateForCurrentDocument(rfh);
  impl->Bind(std::move(receiver));
}
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L259-277)
```text
  StoredCredential credential = FromPasswordForm(std::move(selected_form));
  sync_pb::PasswordSpecificsData specifics_data =
      SpecificsDataFromStoredCredential(credential);

  RemoteActorCredentialSharingService::ShareParameters params;
  params.obfuscated_gaia_id = pending_request_->gaia_id;
  params.web_origin =
      url::Origin::Create(
          GURL(base::StrCat({"https://", pending_request_->domain})))
          .Serialize();
  params.password_data = std::move(specifics_data);
  params.time_to_live = kShareTimeToLive;
  params.task_id = pending_request_->task_id;

  service->SharePassword(
      params,
      base::BindOnce(&RemoteActorCredentialSharingImpl::OnShareCompleted,
                     weak_ptr_factory_.GetWeakPtr(),
                     std::move(pending_request_->callback)));
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L280-316)
```text
bool RemoteActorCredentialSharingImpl::ValidateRequestPreconditions(
    const std::string& gaia_id,
    const std::string& domain,
    const std::string& task_id) {
  content::RenderFrameHost& target_frame = render_frame_host();

  if (!target_frame.IsInPrimaryMainFrame()) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Request from subframe");
    return false;
  }

  if (!target_frame.HasTransientUserActivation()) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Request without user gesture");
    return false;
  }

  auto* policy = content::ChildProcessSecurityPolicy::GetInstance();
  if (!policy->CanAccessDataForOrigin(
          target_frame.GetProcess()->GetID().GetUnsafeValue(),
          target_frame.GetLastCommittedOrigin())) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Process cannot access origin");
    return false;
  }

  if (gaia_id.length() >= kMaxArgumentLength ||
      domain.length() >= kMaxArgumentLength ||
      task_id.length() >= kMaxArgumentLength) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Argument length limit exceeded");
    return false;
  }

  return true;
}
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L385-399)
```text
  pending_request_ = PendingRequest{
      .gaia_id = gaia_id,
      .domain = domain,
      .task_id = task_id,
      .callback = std::move(callback),
      .expected_callbacks = static_cast<int>(stores.size()),
  };

  PasswordFormDigest digest(PasswordForm::Scheme::kHtml,
                            base::StrCat({"https://", domain, "/"}),
                            GURL(base::StrCat({"https://", domain})));

  for (PasswordStoreInterface* store : stores) {
    store->GetLogins(digest, weak_ptr_factory_.GetWeakPtr());
  }
```
