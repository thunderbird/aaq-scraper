// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// web-ext config. Run from this directory:
//   web-ext lint
//   web-ext sign --channel=unlisted --api-key=$AMO_JWT_ISSUER --api-secret=$AMO_JWT_SECRET
// Keeps non-runtime files out of the packaged/signed .xpi.
module.exports = {
  ignoreFiles: [
    "make-icons.py",       // build tool, not shipped
    "console-snippet*.js", // page-console fallbacks, not part of the add-on
    "web-ext-config.cjs",  // this file
    "README.md",
    "web-ext-artifacts",   // signed .xpi output dir
  ],
};
