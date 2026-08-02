# Screenshots

Referenced from the top-level README.

Regenerate with:

```bash
./scripts/capture-screenshots.sh            # against the installed service
./scripts/capture-screenshots.sh http://127.0.0.1:10002   # or a dev server
```

Capture against the **installed** service (`:10001`) rather than a dev server.
The installed one runs as the `strix-dash` user with the tmpfiles grants, so
controls show their real writable state; a dev server runs as you and renders
several of them read-only, which misrepresents the app.

Before committing, check the frame for anything host-identifying — the Host
card shows the hostname and machine model, and the header shows the hostname
beside the wordmark.
