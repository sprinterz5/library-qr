# Deployment Instructions

## 1. Nginx Configuration
The Nginx configuration file is located at `nginx/library.conf`.

### Steps to Apply:
1.  **Copy the config file**:
    Move the file to your Nginx configuration directory (usually `/etc/nginx/conf.d/`).
    ```bash
    sudo cp nginx/library.conf /etc/nginx/conf.d/library.conf
    ```

    > **Trobleshooting: "Permission denied"**
    > If you get a permission error, it's likely because you are trying to write directly to a protected system folder.
    > **Solution:** Copy to your home directory first, then move it with `sudo`.
    >
    > **If uploading from your computer:**
    > 1. Upload `library.conf` to `~/` (your user's home folder) on the server.
    > 2. Then run on the server:
    >    ```bash
    >    sudo mv ~/library.conf /etc/nginx/conf.d/library.conf
    >    ```
    >
    > **If editing directly on the server:**
    > Ensure you use `sudo`:
    > ```bash
    > sudo nano /etc/nginx/conf.d/library.conf
    > ```

2.  **Verify SSL Certificates**:
    Ensure your certificates exist at the paths specified in the config:
    -   Certificate: `/etc/nginx/ssl/full_chain.crt`
    -   Key: `/etc/nginx/ssl/private.key`

    *If your paths differ, edit `/etc/nginx/conf.d/library.conf` on the server.*

3.  **Test Configuration**:
    Run the Nginx configuration test to ensure there are no syntax errors.
    ```bash
    sudo nginx -t
    ```

4.  **Reload Nginx**:
    Apply the changes without dropping connections.
    ```bash
    sudo systemctl reload nginx
    ```

## 2. Docker Middleware
Ensure your Docker container is running and exposing port 8000.

```bash
docker-compose up -d
```

## 3. IP Whitelisting (Granular)
The configuration is now split:
-   **Public**: The main website (`/`) is accessible from **anywhere**.
-   **Restricted**: The `/admin` and `/scan` pages are restricted to the following subnets:
    -   `192.168.0.0/16`
    -   `172.16.0.0/12`
    -   `10.0.0.0/8`
    -   `127.0.0.1`

**Important**: If you need to access `/admin` from home (e.g., dynamic IP), you will need to either:
1.  Connect to the University VPN.

## 4. Troubleshooting: External Access Blocked

If the site works on LAN/WiFi but **fails on 4G/External Networks**, the blockage is likely **NOT** in Nginx (since we removed `deny all` from `/`). It is likely a **Firewall** or **Network Rule**.

### 1. Check Server Firewall (FirewallD)
AlmaLinux uses `firewalld` by default.

Check status:
```bash
sudo firewall-cmd --state
sudo firewall-cmd --list-all
```

**If you don't see `http` and `https` in "services" or ports 80/443:**
You need to open them:
```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

*Note: If `firewalld` is not running, check `iptables` or ask your network admin.*

### 1a. The "Nuclear" Test (Rule out Local Firewall)
If you are unsure if your firewall is mistakenly blocking things, **temporarily stop it** to test:
```bash
sudo systemctl stop firewalld
```
Then try to access the site from your phone.
-   **If it works now**: Your local config was wrong.
-   **If it STILL doesn't work**: It is 100% not you. It is the University/Cloud firewall.

**Don't forget to turn it back on:**
```bash
sudo systemctl start firewalld
```

sudo systemctl start firewalld
```

### 1b. Check for Hidden Firewalls (iptables / SELinux)
Sometimes `firewalld` is off, but raw `iptables` rules or `SELinux` are blocking connection.

**Check raw rules:**
```bash
sudo iptables -L -n -v
```
Look for `DROP` or `REJECT` policies in the `INPUT` chain.

**Check SELinux:**
```bash
sestatus
```
If it says `Enforcing`, it might be blocking Nginx if not configured right.
*Quick test:* `sudo setenforce 0` (Permissive mode). If it works after this, SELinux was the culprit.

### 2. Check Cloud/University Firewall
If you are on a university VM or Cloud Provider (AWS/DigitalOcean/etc), they have an external firewall.
-   **University VM**: Ask IT "Is port 443 open to the public internet for this IP?" often they only open it to the campus network by default.
-   **Cloud**: Check "Security Groups" to ensure Inbound Traffic on Port 443 is set to `0.0.0.0/0`.

### 3. Check Nginx Error Logs
Check if Nginx is actually receiving the request and rejecting it (which would mean a config issue) or if it never sees it (Connection Refused/Timeout = Firewall).

Tail the access log while trying to connect from your phone (4G):
```bash
sudo tail -f /var/log/nginx/access.log
```
-   **If you see lines appear**: Nginx is receiving requests. If you get a 403, check `error.log`.
-   **If NO lines appear**: The request is blocked *before* it hits Nginx -> **It is a Firewall issue**.

