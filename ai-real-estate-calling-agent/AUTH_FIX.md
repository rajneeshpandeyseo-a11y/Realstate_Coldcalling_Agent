# Authentication fix

The management API uses `X-API-Key` and `ADMIN_API_KEY`.

- Swagger/OpenAPI now exposes an `X-API-Key` security scheme, so `/docs` has an **Authorize** button.
- Dashboard Settings now has an **Admin API Authentication** field and stores the key in browser `sessionStorage`.
- The backend still rejects missing/incorrect keys with HTTP 401; authentication has NOT been disabled.
- Local `.env` default is `ADMIN_API_KEY=dev-admin-key` unless you changed it.

## Swagger
1. Open `/docs`.
2. Click **Authorize**.
3. Enter the value of `ADMIN_API_KEY` from `.env` (local default: `dev-admin-key`).
4. Click Authorize and retry the endpoint.

## Dashboard
Open `/dashboard/`, go to Settings, enter the same key, and click Save API Key.
