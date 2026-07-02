# AzerothCore

This cog uses AzerothCore's SOAP interface as its transport by default.

It issues console commands to the server via SOAP (no separate REST bridge or telnet/RA needed).

## What it does

- `ac info` shows general server information.
- `ac online` lists the characters currently online.
- `ac createuser` creates a new account, but only for approved Discord roles.

## Setup

1. Configure the SOAP endpoint the cog should use with `ac set soap_url`.
2. (Optional) Configure SOAP basic auth using `ac set soap_auth <user> <pass>` or include credentials in the URL (see examples).
3. Add the Discord roles that are allowed to create accounts with `ac set accountcreationrole`.

### Helpful examples

- `ac set realmlist wow.sickgaming.net`
- `ac set servername My Realm`
- `ac set createbody {"username":"{username}","password":"{password}"}`

## Expected endpoints

The cog expects these routes by default:

- `GET /status`
- `GET /online`
- `POST /accounts`

If your server exposes SOAP on a non-default host/port, update the SOAP URL accordingly.

### SOAP URL and Basic Auth

AzerothCore's SOAP interface commonly listens on port 17878 in this repository's Docker setup (`DOCKER_SOAP_EXTERNAL_PORT=17878`). If your host IP is `192.168.86.139` and you have a SOAP user `soapuser` with password `abcd1234`, you can set the cog to use it directly in one of two ways:

- Include credentials in the URL (Basic Auth in the URI):

	`ac set soap_url http://soapuser:abcd1234@192.168.86.139:17878/`

- Or set credentials separately (preferred if you don't want credentials in command history):

	`ac set soap_url http://192.168.86.139:17878/`
	`ac set soap_auth soapuser abcd1234`

Either approach will authenticate the cog to the server when performing SOAP requests.

## Using SOAP (examples and Docker notes)

Prefer running the Discord bot on the same Docker Compose network as the AzerothCore containers so you don't need to expose SOAP publicly. If you must access SOAP from the host, ensure the port is published (this repo uses `DOCKER_SOAP_EXTERNAL_PORT=17878` in `.env`).

Docker example (publish SOAP port):

```yaml
services:
  worldserver:
    ports:
      - "${DOCKER_WORLD_EXTERNAL_PORT:-8085}:8085"
      - "${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724"
      - "${DOCKER_SOAP_EXTERNAL_PORT:-17878}:17878"
```

Security note: SOAP credentials are sensitive. Prefer `ac set soap_auth` over embedding passwords in shell history.