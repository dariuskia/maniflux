# Kill the Newsletter on your own server

Your own copy of Kill the Newsletter: each newsletter gets an address like `x7k2p9…@news.yourdomain.com`, and everything sent there shows up in a matching feed that Miniflux on your Mac subscribes to.

It runs on a small rented server because it has to receive email from the internet. One program does everything: the web page, HTTPS (it gets its own certificate from Let's Encrypt), and the mail server.

| Script | Where it runs | What it does |
|---|---|---|
| `install.sh` | server | Downloads, configures and starts it |
| `upgrade.sh` | server | Backs up, upgrades, and rolls back if the new version won't start |
| `new-inbox.sh` | your Mac | Creates an inbox and prints its address and feed URL |
| `backup-to-mac.sh` | your Mac | Copies the server's data to your Mac |

## 1. Rent a server

The cheapest tier at Hetzner Cloud or DigitalOcean is plenty (about $4–6 a month).

- Choose **Ubuntu 24.04**.
- Add your Mac's SSH key when creating it, so you can log in as `root`.
- Note its **public IPv4 address**.

Some providers block *outgoing* mail on new accounts. That doesn't matter here: this only receives mail.

## 2. Pick a hostname and add two DNS records

Use a subdomain of a domain you own, for example `news.yourdomain.com`. It becomes the end of every newsletter address. If you don't have a domain, buy one from any registrar (around $10–15 a year).

At your domain's DNS settings, add:

| Type | Name | Value | Priority |
|---|---|---|---|
| A | `news` | your server's IPv4 address | |
| MX | `news` | `news.yourdomain.com` | 10 |

The A record sends web traffic to the server; the MX record tells other mail servers to deliver `@news.yourdomain.com` email there.

Wait until both show up (usually minutes, sometimes an hour). On your Mac:

```sh
dig +short news.yourdomain.com        # should print the server's IP
dig +short MX news.yourdomain.com     # should print: 10 news.yourdomain.com.
```

## 3. Open the firewall

If your provider has a cloud firewall, allow incoming **22** (SSH), **25** (mail), **80** and **443** (web and certificates). The install script opens the same ports in `ufw` if it's turned on.

## 4. Install

From this folder on your Mac (replace the hostname, IP and email):

```sh
scp install.sh upgrade.sh root@SERVER_IP:
ssh root@SERVER_IP bash install.sh news.yourdomain.com you@gmail.com
```

Your email is given to Let's Encrypt for certificate notices.

Within a minute or two, https://news.yourdomain.com should load. Until the certificate arrives, the logs show the mail server restarting with errors about a missing `.key` file; that's expected and stops on its own. To watch:

```sh
ssh root@SERVER_IP journalctl -u kill-the-newsletter -f
```

## 5. Test it

```sh
./new-inbox.sh "Test"
```

The first time, it asks for your hostname (`news.yourdomain.com`) and saves it in `.hostname` next to the script, so you never type it again.

Send an email to the address it prints (from Gmail is fine), wait a minute, then open the feed URL in your browser. Your email should be in it.

## 6. Use it

For each newsletter:

1. `./new-inbox.sh "Money Stuff"` (or use the form on the website).
2. Subscribe to the newsletter with the printed address. On a Mac it's also copied to your clipboard.
3. Add the feed URL in Miniflux.
4. If the newsletter sends a confirmation email, it appears as the first item in the feed; open it and click the link.

## Backups

All data is in `/root/kill-the-newsletter/data/` on the server. To copy it to your Mac:

```sh
./backup-to-mac.sh
```

Miniflux keeps its own copy of every issue it has fetched, so the server's data matters mostly for keeping your inbox addresses working.

## Upgrades

New versions are listed at https://github.com/leafac/kill-the-newsletter/releases. To upgrade:

```sh
ssh root@SERVER_IP bash upgrade.sh v2.1.4
```

It stops the service, backs up the data to `/root/kill-the-newsletter-backups/`, swaps in the new version and starts it. If it doesn't start, it puts the old version back. Read the changelog first for major versions.

## Good to know

- **Anyone who finds the website can create inboxes on it**, since the form is open. Don't publish the hostname and it's unlikely to matter.
- **Feed URLs aren't password-protected**, but the random ID makes them impractical to guess.
- **It only keeps recent issues:** entries are deleted after one month, or sooner if a feed grows past about 512 KB. Miniflux fetches every hour or so and keeps its own copies, so nothing is lost on your side. Emails over 512 KB are rejected.
- **Mail to addresses that don't match an inbox is rejected**, so spam to made-up addresses goes nowhere.
