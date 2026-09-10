# Deployment boundary

The portfolio mirror is not a production deployment. It contains only variable
names and safe defaults; secrets and service URLs belong in a private platform
configuration. A real deployment should apply migrations in order, enable RLS,
run the backend health check, and provide an authenticated smoke test before
shipping an iOS build.

Optional Gemini review remains server-side and non-authoritative. The app is
usable when the provider is absent or unavailable.
