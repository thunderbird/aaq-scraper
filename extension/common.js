// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.

// Cross-browser namespace shim: Firefox exposes promise-based `browser.*`,
// Chromium only `chrome.*`. This is the only browser-specific line.
const api = globalThis.browser ?? globalThis.chrome;

// Same API base as sumo.py.
const API_BASE = "https://support.mozilla.org/api/2/";

// Host we need access to (match pattern for permissions + host checks).
const SUMO_ORIGIN = "https://support.mozilla.org/*";

// API product slugs. The slug is what goes in the API query AND in the bundle's
// `product` field; import_json.py / default_output_path map "thunderbird" ->
// the "thunderbird-desktop" filename label.
const PRODUCTS = [
  { slug: "thunderbird", label: "Thunderbird Desktop" },
  { slug: "thunderbird-android", label: "Thunderbird Android" },
];
