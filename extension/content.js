// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

// Declared content script (see manifest content_scripts), injected into
// support.mozilla.org pages. This is the PRIMARY fetch path for Firefox: unlike
// scripting.executeScript — which Firefox refuses for this add-on even with the
// host permission granted — a manifest-declared content script is injected by
// the content-script manager, sidestepping that check. The popup messages it;
// it runs aaqFetch (from fetch-core.js, loaded in the same content-script
// context) in the page and replies. Requires the tab to have loaded AFTER the
// add-on was installed/granted, so reload the tab once after install.
const api = globalThis.browser ?? globalThis.chrome;

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || typeof msg.type !== "string") return undefined;
  if (msg.type === "aaq-ping") {
    sendResponse({ href: location.href });
    return undefined;
  }
  if (msg.type === "aaq-fetch") {
    aaqFetch(msg.cfg)
      .then(sendResponse)
      .catch((e) => sendResponse({ error: String((e && e.message) || e) }));
    return true; // keep the channel open for the async sendResponse
  }
  return undefined;
});
