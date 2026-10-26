# Dashboard

Live table of the roles the worker stores in Supabase. New roles appear without a refresh (Supabase Realtime).

```bash
cp .env.example .env.local   # fill in the publishable key
npm install
npm run dev                  # http://localhost:3000
```

The publishable key is public by design: row-level security lets it read `roles` and nothing else.
