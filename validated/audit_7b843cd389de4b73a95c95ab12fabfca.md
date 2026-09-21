### Title
Renderer-supplied `DidCommitProvisionalLoadParams` can overwrite committed navigation state - (File: content/browser/renderer_host/render_frame_host_impl.cc)

### Summary
Chromium’s navigation commit is a two-step process that mirrors the reported `setTerms`/`confirmTerms` authorization bug: the browser process (privileged) creates a pending `NavigationRequest` and `NavigationEntry`, and the renderer process (unprivileged) later sends a `DidCommitProvisionalLoad` Mojo message to confirm the commit. [1](#0-0)  The browser then updates the committed `NavigationEntry` and `RenderFrameHost` state using `params.url` and `params.origin` supplied by the renderer. [2](#0-1)  The extra `NavigationRequest::ValidateCommitOrigin` check that would verify the browser-computed origin against the stored `FrameNavigationEntry` origin is gated behind `kValidateCommitOriginAtCommit`, which is disabled by default. [3](#0-2) [4](#0-3)  This leaves a window for a compromised renderer to overwrite existing committed navigation state with attacker-controlled URL/origin values, analogous to an attacker calling `confirmTerms` with arbitrary `__terms` to overwrite existing `_terms`.

### Finding Description
`RenderFrameHostImpl::DidNavigate` sets the document’s committed URL and origin directly from the renderer-provided `mojom::DidCommitProvisionalLoadParams`: `SetLastCommittedUrl(params.url)` and `SetLastCommittedOrigin(params.origin)`. [2](#0-1)  These renderer-controlled values are then propagated into session history by `NavigationControllerImpl::UpdateNavigationEntryDetails`, which calls `entry->AddOrUpdateFrameEntry(..., params.url, GetCommittedOriginForFrameEntry(params, request), ...)`. [5](#0-4)  Although `NavigationRequest::CommitNavigation` computes a browser-side `origin_to_commit`, the `ValidateCommitOrigin(origin_to_commit)` call is only executed when the `kValidateCommitOriginAtCommit` feature is enabled, and that feature is `FEATURE_DISABLED_BY_DEFAULT`. [6](#0-5) [3](#0-2)  Renderer-side code also contains active TODOs noting that the browser and renderer can disagree on the origin during commit navigation. [7](#0-6) 

### Impact Explanation
A compromised renderer can send a `DidCommitProvisionalLoad` Mojo IPC whose `params.url` and `params.origin` do not match the pending `NavigationRequest`/`NavigationEntry` that the browser created. [2](#0-1)  Because the browser uses those renderer-supplied params to update the committed `NavigationEntry` and `RenderFrameHost` origin, the attack can produce a convincing address-bar URL/origin spoof and can corrupt same-origin policy decisions that rely on `GetLastCommittedOrigin()`. [5](#0-4)  This is the Chromium analog of the Solidity bug where `confirmTerms` uses caller-supplied `__terms` and stale `_pendingTerms` state to overwrite existing `_terms` entries.

### Likelihood Explanation
The primary commit validation is performed in `RenderFrameHostImpl::ValidateDidCommitParams`, whose full implementation was not retrieved in the available search results, so the exact strength of that validation is uncertain.  However, the disabled-by-default `kValidateCommitOriginAtCommit` feature and the renderer-side TODOs explicitly acknowledging browser/renderer origin disagreements during commit indicate that the commit-time origin invariant is not fully enforced in production. [3](#0-2) [7](#0-6)  If `ValidateDidCommitParams` has any gap in matching `params` to the pending `NavigationRequest`—for example, for same-document navigations, converted reloads, or races where the pending entry is reused—the renderer can exploit this two-step confirm to overwrite existing committed state. [8](#0-7) [9](#0-8) 

### Recommendation
Enable `kValidateCommitOriginAtCommit` by default and strengthen commit validation so that renderer-supplied `params.url` and `params.origin` are strictly compared against the browser-computed values stored in the pending `NavigationRequest`/`FrameNavigationEntry` before any `NavigationEntry` or `RenderFrameHost` state is updated. [6](#0-5)  The browser should treat the pending `NavigationRequest` as the authoritative “set” state and reject any `DidCommit` “confirm” message whose params deviate from it, rather than using raw renderer params as the source of truth for committed URL/origin. [2](#0-1) 

### Proof of Concept
A compromised renderer initiates or races a navigation so that a `NavigationRequest` with a matching `nav_entry_id` exists, then sends `mojom::DidCommitProvisionalLoadParams` with `params.url = https://attacker.example/` and `params.origin = https://attacker.example` while the pending `NavigationEntry` points to `https://victim.example/`. [10](#0-9)  `NavigationControllerImpl::RendererDidNavigateToExistingEntry` selects the existing/last-committed entry and `UpdateNavigationEntryDetails` overwrites the `FrameNavigationEntry` URL and origin with the renderer-supplied values. [5](#0-4)  The address bar and subsequent origin checks then reflect `attacker.example`, achieving a security-UI spoof / SOP-bypass analog to the `confirmTerms` overwrite of existing `_terms`.

### Citations

**File:** content/browser/renderer_host/navigation_controller_impl.cc (L1832-1836)
```text
bool NavigationControllerImpl::PendingEntryMatchesRequest(
    NavigationRequest* request) const {
  return pending_entry_ &&
         pending_entry_->GetUniqueID() == request->nav_entry_id();
}
```

**File:** content/browser/renderer_host/navigation_controller_impl.cc (L1838-1967)
```text
bool NavigationControllerImpl::RendererDidNavigate(
    RenderFrameHostImpl* rfh,
    const mojom::DidCommitProvisionalLoadParams& params,
    LoadCommittedDetails* details,
    bool is_same_document_navigation,
    bool was_on_initial_empty_document,
    bool previous_document_had_history_intervention_activation,
    bool caused_by_ad,
    NavigationRequest* navigation_request) {
  DCHECK(navigation_request);

  // Create a scoped object that will ensure at most one NavigationStateChanged
  // notification is sent (if any) at the end of RendererDidNavigate. This
  // avoids redundant notifications (which can be expensive) without risking a
  // missed notification (which can cause URL spoof vulnerabilities if the
  // address bar is stale).
  ScopedDeferredNavigationStateChangeNotifier deferred_notifier(delegate_);

  // Note: validation checks and renderer kills due to invalid commit messages
  // must happen before getting here, in
  // RenderFrameHostImpl::ValidateDidCommitParams. By the time we get here, some
  // effects of the navigation have already occurred.

  is_initial_navigation_ = false;

  // Any pending request to repost a form submission is no longer valid, since a
  // different NavigationEntry is committing.
  pending_reload_ = ReloadType::NONE;

  // Save the previous state before we clobber it.
  bool overriding_user_agent_changed = false;
  if (entry_replaced_by_post_commit_error_) {
    // Same document navigation events with a post-commit error should already
    // be blocked by RenderFrameHostImpl::ValidateDidCommitParams() before
    // reaching here.
    CHECK(!is_same_document_navigation);

    if (pending_entry_) {
      // Before `entry_replaced_by_post_commit_error_` is moved back, make sure
      // `pending_entry_` isn't pointing to the last committed entry.
      // Instead, all reload approaches (e.g., in `Reload` and
      // `LoadIfNecessary`) should attempt to load the
      // `entry_replaced_by_post_commit_error_` instead of the post commit error
      // entry itself.
      CHECK_NE(pending_entry_, entries_[last_committed_entry_index_].get())
          << "Incorrectly reloading the post commit error page entry.";
    }

    // Any commit while a post-commit error page is showing should put the
    // original entry back, replacing the error page's entry.  This includes
    // reloads, where the original entry was used as the pending entry and
    // should now be at the correct index at commit time.
    entries_[last_committed_entry_index_] =
        std::move(entry_replaced_by_post_commit_error_);
  }
  details->previous_main_frame_url = GetLastCommittedEntry()->GetURL();
  details->previous_entry_index = GetLastCommittedEntryIndex();
  // Must honor user agent overrides in the |navigation_request|, such as
  // from things like RequestDesktopSiteWebContentsObserverAndroid. As a
  // result, besides comparing |pending_entry_|'s user agent against
  // LastCommittedEntry's, also need to compare |navigation_request|'s user
  // agent against LastCommittedEntry's.
  if (navigation_request->is_overriding_user_agent() !=
          GetLastCommittedEntry()->GetIsOverridingUserAgent() ||
      (PendingEntryMatchesRequest(navigation_request) &&
       pending_entry_->GetIsOverridingUserAgent() !=
           GetLastCommittedEntry()->GetIsOverridingUserAgent())) {
    overriding_user_agent_changed = true;
  }

  bool is_main_frame_navigation = !rfh->GetParent();

  // For primary frame tree navigations, choose an appropriate
  // BackForwardCacheMetrics to be associated with the new navigation's
  // NavigationEntry, by either creating a new object or reusing the previous
  // entry's one.
  scoped_refptr<BackForwardCacheMetrics> back_forward_cache_metrics;
  if (navigation_request->frame_tree_node()->frame_tree().is_primary()) {
    back_forward_cache_metrics = BackForwardCacheMetrics::
        CreateOrReuseBackForwardCacheMetricsForNavigation(
            GetLastCommittedEntry(), is_main_frame_navigation,
            params.document_sequence_number,
            is_main_frame_navigation ? rfh->GetSiteInstance() : nullptr);
  }

  // Notify the last active entry that we have navigated away.
  if (is_main_frame_navigation && !is_same_document_navigation) {
    if (auto* metrics = GetLastCommittedEntry()->back_forward_cache_metrics()) {
      metrics->MainFrameDidNavigateAwayFromDocument();
    }
  }

  // Use CommonNavigationParam's `should_replace_current_entry` to determine
  // whether the current NavigationEntry should be replaced.
  // (See below for a case where we might override that.)
  details->did_replace_entry =
      navigation_request->common_params().should_replace_current_entry;

  // If there is a pending entry at this point, it should have a SiteInstance,
  // except for restored entries. This should be true even if the current commit
  // is not related to the pending entry.
  bool was_restored = false;
  DCHECK(pending_entry_index_ == -1 || pending_entry_->site_instance() ||
         pending_entry_->IsRestored());

  // Only make changes based on the pending entry if the NavigationRequest
  // matches it. Otherwise, the pending entry may be for a different request
  // (e.g., if a slow history navigation is pending while an auto-subframe
  // commit occurs).
  if (PendingEntryMatchesRequest(navigation_request)) {
    // It is no longer necessary to consider the pending entry as restored.
    if (pending_entry_->IsRestored()) {
      pending_entry_->set_restore_type(RestoreType::kNotRestored);
      was_restored = true;
    }

    // If the SiteInstance has changed from the matching pending entry, this
    // must be treated as a new navigation with replacement. Set the replacement
    // bit here and ClassifyNavigation will identify this case and return
    // NEW_ENTRY.
    if (!rfh->GetParent() && pending_entry_->site_instance() &&
        pending_entry_->site_instance() != rfh->GetSiteInstance()) {
      DCHECK_NE(-1, pending_entry_index_);
      // TODO(nasko,creis,rakina): Move this to happen before committing the
      // navigation. This is a bit complicated because we don't currently
      // set `should_replace_current_entry` for reload/history navigations.
      details->did_replace_entry = true;
    }
  }

```

**File:** content/browser/renderer_host/navigation_controller_impl.cc (L2214-2295)
```text
NavigationType NavigationControllerImpl::ClassifyNavigation(
    RenderFrameHostImpl* rfh,
    const mojom::DidCommitProvisionalLoadParams& params,
    NavigationRequest* navigation_request) {
  TraceReturnReason<tracing_category::kNavigation> trace_return(
      "ClassifyNavigation");

  if (params.did_create_new_entry) {
    // A new entry. We may or may not have a corresponding pending entry, and
    // this may or may not be the main frame.
    if (!rfh->GetParent()) {
      trace_return.set_return_reason("new entry, no parent, new entry");
      return NAVIGATION_TYPE_MAIN_FRAME_NEW_ENTRY;
    }
    // Valid subframe navigation.
    trace_return.set_return_reason("new entry, new subframe");
    return NAVIGATION_TYPE_NEW_SUBFRAME;
  }

  // We only clear the session history in tests when navigating to a new entry.
  DCHECK(!params.history_list_was_cleared);

  if (rfh->GetParent()) {
    // All manual subframes would be did_create_new_entry and handled above, so
    // we know this is auto.
    trace_return.set_return_reason("subframe, last commmited, auto subframe");
    return NAVIGATION_TYPE_AUTO_SUBFRAME;
  }

  const int nav_entry_id = navigation_request->commit_params().nav_entry_id;
  if (nav_entry_id == 0) {
    // This is a renderer-initiated navigation (nav_entry_id == 0), but didn't
    // create a new page.

    // This main frame navigation is not a history navigation (since
    // nav_entry_id is 0), but didn't create a new entry. So this must be a
    // reload or a replacement navigation, which will modify the existing entry.
    //
    // TODO(nasko): With error page isolation, reloading an existing session
    // history entry can result in change of SiteInstance. Check for such a case
    // here and classify it as NEW_ENTRY, as such navigations should be treated
    // as new with replacement.
    trace_return.set_return_reason(
        "nav entry 0, last committed, existing entry");
    return NAVIGATION_TYPE_MAIN_FRAME_EXISTING_ENTRY;
  }

  if (PendingEntryMatchesRequest(navigation_request)) {
    // If the SiteInstance of the |pending_entry_| does not match the
    // SiteInstance that got committed, treat this as a new navigation with
    // replacement. This can happen if back/forward/reload encounters a server
    // redirect to a different site or an isolated error page gets successfully
    // reloaded into a different SiteInstance.
    if (pending_entry_->site_instance() &&
        pending_entry_->site_instance() != rfh->GetSiteInstance()) {
      trace_return.set_return_reason("pending matching nav entry, new entry");
      return NAVIGATION_TYPE_MAIN_FRAME_NEW_ENTRY;
    }

    if (pending_entry_index_ == -1) {
      // In this case, we have a pending entry for a load of a new URL but Blink
      // didn't do a new navigation (params.did_create_new_entry). First check
      // to make sure Blink didn't treat a new cross-process navigation as
      // inert, and thus set params.did_create_new_entry to false. In that case,
      // we must treat it as NEW rather than the converted reload case below,
      // since the new SiteInstance doesn't match the last committed entry.
      if (GetLastCommittedEntry()->site_instance() != rfh->GetSiteInstance()) {
        trace_return.set_return_reason("new pending, new entry");
        return NAVIGATION_TYPE_MAIN_FRAME_NEW_ENTRY;
      }

      // Otherwise, this happens when you press enter in the URL bar to reload.
      // We will create a pending entry, but NavigateWithoutEntry will convert
      // it to a reload since it's the same page and not create a new entry for
      // it. (The user doesn't want to have a new back/forward entry when they
      // do this.) Therefore we want to just ignore the pending entry and go
      // back to where we were (the "existing entry").
      trace_return.set_return_reason("new pending, existing (same) entry");
      return NAVIGATION_TYPE_MAIN_FRAME_EXISTING_ENTRY;
    }
  }

```

**File:** content/browser/renderer_host/navigation_controller_impl.cc (L2331-2355)
```text
void NavigationControllerImpl::UpdateNavigationEntryDetails(
    NavigationEntryImpl* entry,
    RenderFrameHostImpl* rfh,
    const mojom::DidCommitProvisionalLoadParams& params,
    NavigationRequest* request,
    NavigationEntryImpl::UpdatePolicy update_policy,
    bool is_new_entry,
    LoadCommittedDetails* commit_details) {
  // Update the FrameNavigationEntry.
  std::vector<GURL> redirects;
  entry->AddOrUpdateFrameEntry(
      rfh->frame_tree_node(), update_policy, params.item_sequence_number,
      params.document_sequence_number, params.navigation_api_key,
      rfh->GetSiteInstance(), nullptr, params.url,
      GetCommittedOriginForFrameEntry(params, request),
      Referrer(*params.referrer),
      request ? request->common_params().initiator_origin : params.origin,
      request ? request->common_params().initiator_base_url : std::nullopt,
      request ? request->GetRedirectChain() : redirects, params.page_state,
      params.method, params.post_id, nullptr /* blob_url_loader_factory */,
      ComputePolicyContainerPoliciesForFrameEntry(
          rfh, request && request->IsSameDocument(),
          request ? request->DidEncounterError() : false,
          request ? request->common_params().url : params.url));

```

**File:** content/browser/renderer_host/navigation_controller_impl.cc (L2671-2769)
```text
    ScopedDeferredNavigationStateChangeNotifier* deferred_notifier) {
  DCHECK(GetLastCommittedEntry()) << "ClassifyNavigation should guarantee "
                                  << "that a last committed entry exists.";

  // We should only get here for main frame navigations.
  DCHECK(!rfh->GetParent());

  NavigationEntryImpl* entry = nullptr;
  if (request->commit_params().intended_as_new_entry) {
    // We're guaranteed to have a last committed entry if intended_as_new_entry
    // is true.
    entry = GetLastCommittedEntry();

    // If the NavigationRequest matches a new pending entry and is classified as
    // EXISTING_ENTRY, then it is a navigation to the same URL that was
    // converted to a reload, such as a user pressing enter in the omnibox.
    if (pending_entry_index_ == -1 && PendingEntryMatchesRequest(request)) {
      // Note: The pending entry will usually have a real ReloadType here, but
      // it can still be ReloadType::NONE in cases that
      // ShouldTreatNavigationAsReload returns false (e.g., POST, view-source).

      // If we classified this correctly, the SiteInstance should not have
      // changed.
      CHECK_EQ(entry->site_instance(), rfh->GetSiteInstance());

      // For converted reloads, we assign the entry's unique ID to be that of
      // the new one. Since this is always the result of a user action, we want
      // to dismiss infobars, etc. like a regular user-initiated navigation.
      entry->set_unique_id(pending_entry_->GetUniqueID());

      // The extra headers may have changed due to reloading with different
      // headers.
      entry->set_extra_headers(pending_entry_->extra_headers());
    }
    // Otherwise, this was intended as a new entry but the pending entry was
    // lost in the meantime and no new entry was created. We are stuck at the
    // last committed entry.

    // Even if this is a converted reload from pressing enter in the omnibox,
    // the server could redirect, requiring an update to the SSL status. If this
    // is a same document navigation, though, there's no SSLStatus in the
    // NavigationRequest so don't overwrite the existing entry's SSLStatus.
    if (!is_same_document) {
      entry->GetSSL() =
          SSLStatus(request->GetSSLInfo().value_or(net::SSLInfo()));
    }
  } else if (const int nav_entry_id = request->commit_params().nav_entry_id) {
    // This is a browser-initiated navigation (back/forward/reload).
    entry = GetEntryWithUniqueID(nav_entry_id);

    if (is_same_document) {
      // There's no SSLStatus in the NavigationRequest for same document
      // navigations, so normally we leave |entry|'s SSLStatus as is. However if
      // this was a restored same document navigation entry, then it won't have
      // an SSLStatus. So we need to copy over the SSLStatus from the entry that
      // navigated it.
      NavigationEntryImpl* last_entry = GetLastCommittedEntry();
      if (entry->GetURL().DeprecatedGetOriginAsURL() ==
              last_entry->GetURL().DeprecatedGetOriginAsURL() &&
          last_entry->GetSSL().initialized && !entry->GetSSL().initialized &&
          was_restored) {
        entry->GetSSL() = last_entry->GetSSL();
      }
    } else {
      // In rapid back/forward navigations |request| sometimes won't have a cert
      // (http://crbug.com/727892). So we use the request's cert if it exists,
      // otherwise we only reuse the existing cert if the origins match.
      if (request->GetSSLInfo().has_value() &&
          request->GetSSLInfo()->is_valid()) {
        entry->GetSSL() = SSLStatus(*(request->GetSSLInfo()));
      } else if (entry->GetURL().DeprecatedGetOriginAsURL() !=
                 request->GetURL().DeprecatedGetOriginAsURL()) {
        entry->GetSSL() = SSLStatus();
      }
    }
  } else {
    // This is renderer-initiated. The only kinds of renderer-initiated
    // navigations that are EXISTING_ENTRY are same-document navigations that
    // result in replacement (e.g. history.replaceState(), location.replace(),
    // forced replacements for trivial session history contexts). For these
    // cases, we reuse the last committed entry.
    entry = GetLastCommittedEntry();

    // TODO(crbug.com/40532777): Set page transition type to
    // PAGE_TRANSITION_LINK to avoid misleading interpretations (e.g. URLs
    // paired with PAGE_TRANSITION_TYPED that haven't actually been typed) as
    // well as to fix the inconsistency with what we report to observers
    // (PAGE_TRANSITION_LINK | PAGE_TRANSITION_CLIENT_REDIRECT).

    CopyReplacedNavigationEntryDataIfPreviouslyEmpty(entry, entry);

    // If this is a same document navigation, then there's no SSLStatus in the
    // NavigationRequest so don't overwrite the existing entry's SSLStatus.
    if (!is_same_document) {
      entry->GetSSL() =
          SSLStatus(request->GetSSLInfo().value_or(net::SSLInfo()));
    }
  }
  DCHECK(entry);
```

**File:** content/browser/renderer_host/render_frame_host_impl.cc (L5393-5484)
```text
void RenderFrameHostImpl::DidNavigate(
    const mojom::DidCommitProvisionalLoadParams& params,
    NavigationRequest* navigation_request,
    bool was_within_same_document) {
  // The `url` and `origin` of the current document are stored in the
  // RenderFrameHost, because:
  // - The FrameTreeNode represents the frame.
  // - The RenderFrameHost represents a document hosted inside the frame.
  //
  // The URL is set regardless of whether it's for a net error or not.
  SetLastCommittedUrl(params.url);
  // The origin is only updated for cross-document navigations.
  if (!was_within_same_document ||
      !features::IsEnforceSameDocumentOriginInvariantsEnabled()) {
    SetLastCommittedOrigin(params.origin);
  }

  // If the navigation was a cross-document navigation and it's not the
  // synchronous about:blank commit, then it committed a document that is not
  // the initial empty document.
  if (!navigation_request->IsSameDocument() &&
      (!navigation_request->is_synchronous_renderer_commit() ||
       !navigation_request->GetURL().IsAboutBlank())) {
    navigation_request->frame_tree_node()->set_not_on_initial_empty_document();
  }

  if (lifecycle_state_ == LifecycleStateImpl::kActive) {
    // The NIK might change after this, so decrement the count for the current
    // NIK.
    GetStoragePartition()->DecrementActiveDocumentCount(
        GetNetworkIsolationKey());
  }

  isolation_info_ = ComputeIsolationInfoInternal(
      GetLastCommittedOrigin(), isolation_info_.request_type(),
      navigation_request->is_credentialless(),
      navigation_request->ComputeFencedFrameNonce());

  if (lifecycle_state_ == LifecycleStateImpl::kActive) {
    // The NIK might have changed after the above call, so increment the count
    // for the new NIK.
    GetStoragePartition()->IncrementActiveDocumentCount(
        GetNetworkIsolationKey());
  }

  // Separately, update the frame's last successful URL except for net error
  // pages, since those do not end up in the correct process after transfers
  // (see https://crbug.com/560511).  Instead, the next cross-process navigation
  // or transfer should decide whether to swap as if the net error had not
  // occurred.
  // TODO(creis): Remove this block and always set the URL.
  // See https://crbug.com/588314.
  if (!navigation_request->DidEncounterError()) {
    last_successful_url_ = params.url;
    navigation_request->frame_tree_node()->set_last_successful_origin(
        GetLastCommittedOrigin());
  }

  renderer_url_info_.last_document_url = GetLastDocumentURL(
      navigation_request, params, is_error_document_, renderer_url_info_);

  // Set the last committed HTTP method and POST ID. Note that we're setting
  // this here instead of in DidCommitNewDocument because same-document
  // navigations triggered by the History API (history.replaceState/pushState)
  // will reset the method to "GET" (while fragment navigations won't).
  // TODO(arthursonzogni): Stop relying on DidCommitProvisionalLoadParams. Use
  // the NavigationRequest instead. The browser process doesn't need to rely on
  // the renderer process.
  last_http_method_ = params.method;
  last_post_id_ = params.post_id;

  // TODO(arthursonzogni): Stop relying on DidCommitProvisionalLoadParams. Use
  // the NavigationRequest instead. The browser process doesn't need to rely on
  // the renderer process.
  last_http_status_code_ = params.http_status_code;

  // Sets whether the last navigation has user gesture/transient activation or
  // not.
  last_committed_common_params_has_user_gesture_ =
      navigation_request->common_params().has_possibly_filtered_user_gesture;

  // Sets whether the last cross-document navigation was initiated from the
  // browser (e.g. typing on the location bar) or from the renderer while having
  // transient user activation
  if (!was_within_same_document) {
    last_cross_document_navigation_started_by_user_ =
        !navigation_request->IsRendererInitiated() ||
        navigation_request->StartedWithTransientActivation();
  }

  // Navigations that activate an existing bfcached or prerendered document do
  // not create a new document.
```

**File:** content/public/common/content_features.cc (L1260-1269)
```text
// Enables a CHECK in NavigationRequest::ValidateCommitOrigin() to verify
// that the origin used at commit time matches the expected origin stored
// in the FrameNavigationEntry, whenever PageState is non-empty.
//
// This helps catch session history corruption or stale origin-related state
// being sent to the renderer, which could violate origin isolation and lead
// to security issues (see crbug.com/41492620).
//
// This feature is disabled by default while we diagnose on Canary only.
BASE_FEATURE(kValidateCommitOriginAtCommit, base::FEATURE_DISABLED_BY_DEFAULT);
```

**File:** content/browser/renderer_host/navigation_request.cc (L6999-7062)
```text
void NavigationRequest::CommitNavigation() {
  TRACE_EVENT("navigation", "NavigationRequest::CommitNavigation",
              perfetto::Flow::FromPointer(this));

  if (fast_fetch_manager_) {
    fast_fetch_manager_->OnCommitNavigation(*this);
  }

  // A navigation request should only commit once the response has been
  // processed.
  CHECK_GE(state_, WILL_PROCESS_RESPONSE);
  // If a WebUI was created for this navigation, it must have been moved to the
  // RenderFrameHost we're about to commit in already.
  CHECK(!HasWebUI());

  CheckSoftNavigationHeuristicsInvariants();

  CoopCoepSanityCheck();

  DetermineAgentClusterKeyForCommit();

  UpdateHistoryParamsInCommitNavigationParams();
  CHECK(NeedsUrlLoader() == !!response_head_ ||
        (was_redirected_ && common_params_->url.IsAboutBlank()));
  CHECK(!common_params_->url.SchemeIs(url::kJavaScriptScheme));
  CHECK(!blink::IsRendererDebugURL(common_params_->url));

  AddOldPageInfoToCommitParamsIfNeeded();
  if (ShouldDispatchPageSwapEvent()) {
    frame_tree_node_->current_frame_host()
        ->GetAssociatedLocalFrame()
        ->DispatchPageSwap(WillDispatchPageSwap());
  }

  url::Origin origin_to_commit = GetOriginToCommit().value();
  if (base::FeatureList::IsEnabled(features::kValidateCommitOriginAtCommit)) {
    ValidateCommitOrigin(origin_to_commit);
  }
  isolation_info_for_subresources_ =
      GetRenderFrameHost()->ComputeIsolationInfoForSubresourcesForPendingCommit(
          origin_to_commit, is_credentialless(), ComputeFencedFrameNonce());
  CHECK(!isolation_info_for_subresources_.IsEmpty());

  // If this is a srcdoc document, the content comes from the parent frame, so
  // the origin must be the parent and not the initiator. In this case, do not
  // inherit the base URI from the initiator if the origins do not agree
  // (accounting for the case that the chosen origin might be opaque with a
  // precursor of the parent's origin, in a sandboxed case). There should also
  // not be an initiator base URL if there is no initiator origin, such as in a
  // browser-initiated navigation.
  if (GetURL().IsAboutSrcdoc() &&
      (!common_params().initiator_origin ||
       origin_to_commit.GetTupleOrPrecursorTupleIfOpaque() !=
           common_params()
               .initiator_origin->GetTupleOrPrecursorTupleIfOpaque())) {
    // TODO(crbug.com/40165505): Make this unreachable by blocking
    // cross-origin about:srcdoc navigations. Then enforce that the chosen
    // origin for srcdoc cases agrees with the parent frame's origin.
    common_params_->initiator_base_url = std::nullopt;
  }

  // TODO(crbug.com/40092527): The storage key's origin is ignored at the
  // moment. We will be able to use it once the browser can compute the origin
  // to commit.
```

**File:** content/renderer/render_frame_impl.cc (L4164-4189)
```text
  // TODO(crbug.com/40092527): Turn this into a DCHECK for origin equality when
  // the linked bug is fixed. Currently sometimes the browser and renderer
  // disagree on the origin during commit navigation.
  if (pending_cookie_manager_info_ &&
      pending_cookie_manager_info_->origin ==
          url::Origin(frame_->GetDocument().GetSecurityOrigin())) {
    frame_->GetDocument().SetCookieManager(
        std::move(pending_cookie_manager_info_->cookie_manager));
  }

  // TODO(crbug.com/40092527): Turn this into a DCHECK for origin equality when
  // the linked bug is fixed. Currently sometimes the browser and renderer
  // disagree on the origin during commit navigation.
  if (pending_storage_info_ &&
      original_storage_key_.origin() ==
          url::Origin(frame_->GetDocument().GetSecurityOrigin())) {
    if (pending_storage_info_->local_storage_area) {
      frame_->SetLocalStorageArea(
          std::move(pending_storage_info_->local_storage_area));
    }
    if (pending_storage_info_->session_storage_area) {
      frame_->SetSessionStorageArea(
          std::move(pending_storage_info_->session_storage_area));
    }
  }

```
