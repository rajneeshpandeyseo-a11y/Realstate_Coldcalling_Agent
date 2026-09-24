# Local Swagger authentication fix

In development, management API routes accept requests from localhost (127.0.0.1, localhost, ::1) without requiring X-API-Key. Non-local development requests and all non-development environments still require X-API-Key.

This allows Swagger at http://127.0.0.1:8000/docs to execute POST /api/v1/leads without manual authorization while preserving production authentication.
