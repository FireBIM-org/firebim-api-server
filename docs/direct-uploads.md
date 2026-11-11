# Direct IFC uploads

The API signs private storage PUT URLs for the browser. The storage endpoint must
be reachable from the browser, and bucket CORS must allow its exact origin.

For remote access at `http://100.95.221.15:3000`, set these in the API server's
local `.env`:

```dotenv
S3_PUBLIC_ENDPOINT_URL=http://100.95.221.15:9000
S3_BROWSER_CORS_ORIGINS=http://localhost:3000,http://100.95.221.15:3000
```

Keep `S3_ENDPOINT_URL` pointed at the internal storage endpoint. Use the actual
browser-reachable storage host and exact browser origins for other deployments;
do not use wildcard origins. The browser must be able to reach storage port 9000.
HTTP remains unencrypted; use HTTPS for secure transport.

Apply the settings and bucket CORS by recreating only the API service:

```sh
docker compose up -d --no-deps --force-recreate api
```

Start a new upload afterward: existing sessions retain their old signed URLs.
Do not rewrite signed URL hosts in the browser, because the host is part of the
SigV4 signature.

## Verify against real storage

Run the smoke check with the same storage settings and browser origin:

```sh
PYTHONPATH=. .venv/bin/python scripts/check_direct_upload.py --origin http://100.95.221.15:3000
```

The check creates and removes a disposable private bucket, leaving the configured
bucket unchanged. It requires bucket creation/deletion and CORS permissions and
verifies preflight, signed PUT, checksum readback, immutable snapshots, denied
origins, anonymous access denial, and signature invalidation when the Host changes.
Also verify that the configured bucket's preflight allows the remote origin and
that the API's signing endpoint uses the public host; the disposable bucket check
does not validate the configured bucket's CORS rules.