PLACEHOLDER
====================================================================
This directory is reserved for the Apple Sign-In domain association file.

When you complete Step 1.3 of /app/memory/APPLE_SIGNIN_SETUP.md, Apple will
let you download a file named:

  apple-developer-domain-association.txt

Place that file in THIS directory (replacing this README) and redeploy.
Apple's verifier will fetch it from:

  https://aiclonechats.com/.well-known/apple-developer-domain-association

…and the domain will turn from "Pending" → "Verified" in your Apple
Developer console.

After Apple confirms verification, this file is no longer required, but
there's no harm in keeping it in place.
