---
type: pattern
importance: 3
reinforcement: 1
created: '2026-02-11'
updated: '2026-02-13'
status: active
---
- Stripe webhook requires signature verification before processing events; use verify_header method
- Fixed N+1 query in order-service using selectinload(Order.items)
- FastAPI pagination pattern: PaginatedResponse[T] generic with items + next_cursor + has_more
- orders table has idx_orders_created_at index, suitable for cursor pagination sorting
