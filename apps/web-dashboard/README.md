# Moving Target Web

React/Next.js version of the Moving Target OSM Dashboard, intended for Vercel.

The original Python app talks to local MacBook/Android hardware: ADB, traceroute,
Wi-Fi system commands, browser sensors, Redis, and local OSM extracts. A Vercel
deployment cannot access that local hardware directly. This app is structured as
the internet-facing authenticated dashboard; data should arrive through a cloud
bridge API, Redis-compatible cloud store, or periodic upload from the Python app.

## Local Development

```bash
cp .env.example .env.local
npm install
npm run dev:public
```

Open `http://127.0.0.1:3000`.

There are two local modes:

```bash
npm run dev:public
npm run dev:auth
```

- Public/no-auth dashboard: `http://127.0.0.1:3000`
- Google-auth dashboard: `http://127.0.0.1:3001`

The public port sets `AUTH_REQUIRED=false` and never calls Google OAuth. The
auth port sets `AUTH_REQUIRED=true` and requires real Google OAuth credentials.

## Google Authentication

1. Create a Google Cloud project.
2. Configure OAuth consent screen.
3. Create an OAuth Web Client.
4. Add authorized redirect URI:

```text
http://127.0.0.1:3001/api/auth/callback/google
http://localhost:3001/api/auth/callback/google
https://YOUR_VERCEL_DOMAIN/api/auth/callback/google
```

5. For the local auth port, set these values in `.env.local`:

```text
AUTH_REQUIRED=true
AUTH_SECRET=<random 32+ byte secret>
AUTH_URL=http://127.0.0.1:3001
NEXTAUTH_URL=http://127.0.0.1:3001
AUTH_GOOGLE_ID=<real Google OAuth client id>
AUTH_GOOGLE_SECRET=<real Google OAuth client secret>
```

6. Set these Vercel environment variables:

```text
AUTH_REQUIRED
AUTH_SECRET
AUTH_URL
AUTH_GOOGLE_ID
AUTH_GOOGLE_SECRET
NEXT_PUBLIC_ALLOWED_DOMAIN
MOVING_TARGET_API_URL
MOVING_TARGET_API_TOKEN
```

`NEXT_PUBLIC_ALLOWED_DOMAIN` is optional. If set, only users whose email ends
with that domain can access the app.

## Vercel Deploy

```bash
npm install -g vercel
vercel
vercel env add AUTH_SECRET
vercel env add AUTH_GOOGLE_ID
vercel env add AUTH_GOOGLE_SECRET
vercel env add NEXT_PUBLIC_GOOGLE_MAPS_API_KEY
vercel --prod
```

## Data Bridge

`app/api/dashboard/route.ts` currently returns mock data unless
`MOVING_TARGET_API_URL` is configured. Point that variable at a cloud endpoint
that returns the Python dashboard JSON shape:

```json
{
  "samples": [],
  "status": {},
  "settings": {},
  "route_tracks": []
}
```

## Android Auto Scaffold

The `../android-auto/` folder contains a starter Android Auto project skeleton.
Android Auto apps are not deployed through Vercel; they are packaged as Android
apps and must follow Android for Cars app category and template restrictions.
