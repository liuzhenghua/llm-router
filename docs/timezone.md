# Timezone Handling

## Summary

- **All `DATETIME` fields in the database are stored in UTC**, regardless of any timezone configuration.
- **Semantic date fields** (`billing_date`, `summary_date`, `daily_spend_date`) are computed using the **per-API-key timezone** to determine "today."
- The global `TZ` setting acts as the **default timezone for newly created API keys**, not as a per-request override.

---

## What Is Stored in UTC

The following fields always store UTC, no matter what timezone is configured:

| Table | Field | Meaning |
|-------|-------|---------|
| `api_keys` | `created_at`, `updated_at`, `deleted_at` | Record lifecycle timestamps |
| `request_logs` | `started_at`, `ended_at`, `created_at` | When the HTTP request was processed |
| `balance_ledgers` | `created_at` | When the balance transaction was recorded |
| `daily_usage_summaries` | `created_at`, `updated_at` | Record lifecycle timestamps |

These are **point-in-time events** — they represent "when something happened" and have no relationship to any calendar day boundary.

---

## What Uses the API Key's Timezone

The following fields represent a **calendar date** and are computed using the API key's `timezone`:

| Table | Field | Meaning |
|-------|-------|---------|
| `api_keys` | `daily_spend_date` | The last calendar day on which daily spend was reset |
| `usage_records` | `billing_date` | The calendar date on which the request was billed |
| `daily_usage_summaries` | `summary_date` | The calendar date for the daily cost rollup |

**Why this matters:** If a user in `Asia/Shanghai` makes a request at `2025-01-16 23:30 UTC` (which is `2025-01-17 07:30 CST`), the billing should appear under `2025-01-17`, not `2025-01-16`.

---

## Configuration

### Global Default (`TZ` env variable)

```env
# .env
TZ=Asia/Shanghai
```

- Sets the timezone that **newly created API keys inherit** when no per-key timezone is specified.
- Defaults to `UTC` if not set.
- Changing this value does **not** retroactively affect existing keys.

### Per-API-Key Timezone

Each API key has a `timezone` field (IANA format). It can be set when creating or editing a key in the admin UI or via the API.

```
Asia/Shanghai     # China Standard Time (UTC+8)
America/New_York  # Eastern Time (UTC-5 / UTC-4 DST)
Europe/London     # GMT / BST
UTC               # No offset (default)
```

---

## Scenarios

### Scenario 1 — Single region, all keys share one timezone

You run the router in China and all users are on China Standard Time. Set:

```env
TZ=Asia/Shanghai
```

All new API keys automatically get `timezone = Asia/Shanghai`. No per-key configuration needed. Daily reports and budget resets roll over at midnight CST.

---

### Scenario 2 — Multi-tenant SaaS with users in different timezones

Each tenant has their own API key. User A is in Shanghai, User B is in New York.

- Create key for User A with `timezone = Asia/Shanghai`
- Create key for User B with `timezone = America/New_York`

When User A makes a request at `2025-01-16 22:00 UTC` (next day in Shanghai: `2025-01-17 06:00 CST`), their billing_date is `2025-01-17`. User B's billing date for the same request is `2025-01-16` (still Tuesday evening in New York). Each user's daily budget resets at their own local midnight.

---

### Scenario 3 — Mixed: some keys have custom timezone, others use default

You deploy with `TZ=UTC` (the default). Most keys don't have a specific timezone set, so they all use UTC. For a specific enterprise customer in Tokyo, you explicitly set their key's `timezone = Asia/Tokyo`. Only that key's billing and budget reset at Tokyo midnight; all others use UTC midnight.

---

### Scenario 4 — Migrating existing keys from UTC to a local timezone

After deploying with `TZ=UTC`, you decide to switch to `Asia/Shanghai` for better reporting. Run a database update:

```sql
-- SQLite
UPDATE lr_api_keys SET timezone = 'Asia/Shanghai' WHERE timezone = 'UTC';

-- MySQL
UPDATE lr_api_keys SET timezone = 'Asia/Shanghai' WHERE timezone = 'UTC';
```

Then update your `.env`:
```env
TZ=Asia/Shanghai
```

Historical `billing_date` and `summary_date` values **are not modified** — they reflect the date as it was computed at request time. Only future requests use the new timezone.

---

## Why Keep the Global `TZ` Setting?

The global `TZ` is the **system-wide default** — it determines what timezone newly created API keys inherit when no explicit timezone is provided. It is *not* used at request time for any per-key logic.

Without this default, every key creation would need an explicit timezone, which is impractical when all your users are in the same region. Setting `TZ=Asia/Shanghai` once means every new key automatically gets China Standard Time without extra configuration.
