---
type: decision
importance: 5
reinforcement: 2
created: '2026-02-11'
updated: '2026-02-14'
status: active
---
- Use async queue for webhook event processing to avoid Stripe timeout retries
- Use Stripe idempotency keys to prevent duplicate charges
- JWT + refresh token for auth: 15min access token, 7day refresh token
- Redis for token blacklist storage
- All list APIs use cursor-based pagination (offset has performance issues at scale)
