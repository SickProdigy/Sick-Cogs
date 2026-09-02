# SickWallet WebSocket Broker

This process is part of the companion website deployment. It accepts the CryptoWallet cog's
authenticated outbound WebSocket connection; it is not a wallet backend and must never receive
CDP API keys, wallet secrets, Discord OAuth secrets, JWT signing keys, private keys, or recovery
phrases.

## Runtime

Use a dedicated Python virtual environment. The broker's dependency is isolated from Red's Python
environment.

```bash
python3 -m venv /opt/sickwallet-broker/venv
/opt/sickwallet-broker/venv/bin/pip install -r requirements.txt
install -d -m 0700 /var/lib/sickwallet-broker
export SICKWALLET_BROKER_DATABASE=/var/lib/sickwallet-broker/broker.sqlite3
/opt/sickwallet-broker/venv/bin/python app.py serve
```

Keep the listener on `127.0.0.1:8790`. Publish it only through the existing HTTPS nginx/Apache
virtual host:

```nginx
location = /cryptowallet/broker/health {
    proxy_pass http://127.0.0.1:8790/health;
    proxy_set_header Host $host;
}

location = /cryptowallet/broker/v1/pair {
    proxy_pass http://127.0.0.1:8790/v1/pair;
    proxy_set_header Host $host;
}

location = /cryptowallet/socket {
    proxy_pass http://127.0.0.1:8790/v1/socket;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 75s;
    proxy_send_timeout 75s;
}
```

Generate a one-time code on the website server:

```bash
export SICKWALLET_BROKER_DATABASE=/var/lib/sickwallet-broker/broker.sqlite3
/opt/sickwallet-broker/venv/bin/python app.py pair-code
```

The code expires after ten minutes and is consumed atomically. The cog-side pairing command is the
next implementation checkpoint. Do not paste the future durable installation credential into
Discord or the website document root.

The current foundation implements pairing, authenticated WebSocket upgrades, replay protection,
bounded messages, heartbeat handling, and one active connection per installation. Routing
browser/OAuth operations over the socket is intentionally deferred until the cog client is in
place.
