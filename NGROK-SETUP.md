# Public access: marinodigita.com

The Android APK uses https://www.marinodigita.com/login with the office address as fallback.
Changing the app URL does not activate the public domain.

1. In your ngrok account, reserve www.marinodigita.com as a custom domain (ngrok Pay-as-you-go plan).
2. Copy the DNS CNAME target shown by ngrok for this domain.
3. In Cloudflare DNS, configure the www CNAME to that exact target. Set DNS only (grey cloud), not Proxied. Check existing DNS records before replacing anything; preserve unrelated services and email records.
4. Configure the ngrok agent using your own account authtoken locally. Do not share the token in chat.
5. Run run-server.bat. The local server must use the updated server.py and auth.json with admin/viewer users.
6. Run start-ngrok.bat and keep both processes running on the office PC.
7. Verify https://www.marinodigita.com/api/health returns {"ok": true}, and /login displays the login form.
8. Install app.apk. Turn off Wi-Fi and verify login, viewing, admin saving and viewer refusal on 4G.

The Cloudflare dashboard URL alone does not provide account access. The custom domain and DNS configuration must be completed before the URL works.

ngrok docs: https://ngrok.com/docs/gateway/domains/custom-domains

ngrok does not support apex domains. Use www.marinodigita.com for the tunnel. If desired, set up an HTTPS redirect from marinodigita.com to www.marinodigita.com in Cloudflare after the tunnel is working.
