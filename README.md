# Homelab

Homelab running k3s, managed by [Flux CD](https://fluxcd.io/) for GitOps.

## Stack

- [K3s](https://k3s.io) as Kubernetes distribution
- [Flux CD](https://fluxcd.io) for GitOps
- [Traefik](https://traefik.io/traefik/) as reverse proxy
- [cert-manager](https://cert-manager.io) for TLS via Let's Encrypt
- [MetalLB](https://metallb.universe.tf) as load balancer
- [Authelia](https://www.authelia.com) + [LLDAP](https://lldap.com) for SSO and auth
- [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age) to keep things secret
- [Renovate](https://mend.io/renovate) to keep things up to date

## Repo structure

```
apps/<name>/               Per-app manifests
clusters/homelab/          Flux entrypoint
compose/                   VPS Docker Compose stack
infrastructure/
  controllers/             Helm releases and repos
  configs/                 Certs, middleware, policies, etc.
```

Flux reconciles in order: `controllers` -> `configs` -> `apps`.

## Setup

### Prerequisites

- k3s cluster
- `flux`, `sops`, and `age` installed locally
- Fine-grained GitHub PAT:
    - Administration: Read / Write
    - Contents: Read / Write
    - Metadata: Read-only

### Bootstrap

```sh
# Bootstrap Flux
flux bootstrap github \
  --owner=<github-username> \
  --repository=<repo-name> \
  --path=clusters/homelab \
  --personal

# Create the SOPS decryption secret
cat ~/.config/sops/age/keys.txt | kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=/dev/stdin

# Create cluster settings (not committed to git)
kubectl create configmap cluster-settings -n flux-system \
  --from-literal=ACME_EMAIL=<email>
  --from-literal=DNSPROXY_LB_IP=<ip> \
  --from-literal=DOMAIN=<domain> \
  --from-literal=FRP_SERVER_ADDR=<email> \
  --from-literal=FRP_GROUP_KEY=<email> \
  --from-literal=IMMICH_LB_IP=<ip> \
  --from-literal=LDAP_BASE_DN=<dc=example,dc=com> \
  --from-literal=METALLB_RANGE=<ip-start>-<ip-end>
```

Flux reconciles everything automatically after this.

### Editing secrets

```sh
sops apps/<name>/secrets.yaml
```

The age private key must be at `~/.config/sops/age/keys.txt` locally and in the `sops-age` secret in `flux-system` on the cluster.

## VPS

This setup relies the following Docker Compose stack on a VPS:

- [Caddy](https://caddyserver.com) as reverse proxy
- [frp](https://github.com/fatedier/frp) for tunneling
- [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome) for DNS
- [Uptime Kuma](https://uptimekuma.org/) for monitoring

There's nothing fancy going on here. VPS comes with a public IP which allows using DNS outside of local network. Caddy and frp
together replace the previously used Cloudflare tunnel setup. Uptime Kuma lives on the VPS as well for reporting the status.

### Setup

```sh
cd compose
cp .env.example .env
cp frps/frps.toml.example frps/frps.toml
docker compose up -d
```

Don't forget to update `frps.toml` and `.env` with your own values.
