# Foodics Purchasing (`foodics_purchase`)

Odoo-driven purchase lifecycle between Odoo 18 and Foodics. **Odoo is the
source of truth**: it buys, it receives, and every real stock event is
mirrored to Foodics through the API.

```
foodics_base               foundation: connection/OAuth/branches (NOT modified)
foodics_purchase           THIS MODULE - purchase logic only
foodics_mock               fake Foodics server (testing only)
foodics_mock_purchase_ext  adds fake /suppliers /purchase_orders ... (testing only)
```

All HTTP goes through one adapter (`models/foodics_api.py`). Testing today:
point the connection at the mock; going live later: clear the two
*Custom … Base URL* fields and authorize with real credentials — **zero code
changes**.

## Coverage (traced to the API Docs PDFs)

| Foodics concept | Doc section | Implementation |
|---|---|---|
| Purchase Order CRUD | `resources/purchase_orders` | Confirm → `POST` (Draft 1 / Pending 2 + submitter), Cancel → `DELETE` (draft) or `PUT status=4`, status mirror via polling |
| PO statuses 1..6 | Statuses table | `foodics_state` mirror; Partially Received(5)/Closed(6) after receipts |
| Receiving = Inventory Transaction **type 1** | ERP guide: “rely solely on `/inventory_transactions`” | Receipt validation pushes a Closed(4) Purchasing TX referencing `purchase_order_id`; one TX per picking → partial deliveries supported naturally |
| Return to Supplier = type 4 | Types table | Return pickings of synced receipts push a type-4 TX |
| Suppliers | `resources/suppliers` | Pull + upsert into `foodics.supplier` linked to auto-matched/created `res.partner`; pivots (order_unit, order→storage factor, MOQ) drive payloads & validation; soft-deletes archive locally |
| Supplier item attach | `POST /suppliers/{id}/items/{item}` | Validation error tells you to fix mapping when pivot missing |
| Inventory Items | `resources/inventory_items` | Pull + upsert `foodics.inventory.item` linked to `product.product`; optional product auto-create; UoM guessing from unit strings |
| Cost Adjustment Transactions | `resources/cost_adjustment_transactions` | Poller updates item cost (and optionally product standard price) using `updated_after` cursor |
| Inventory Levels | `GET /inventory_levels/{branch_id}` | Per-branch snapshots in `foodics.inventory.level`, below-minimum flags |
| Pagination | core/pagination | Adapter walks `meta.last_page`, 50/page |
| Errors 401/403/404/422/429/500/503 | core/errors | Typed exceptions; 429/network → retry queue with backoff; 401 → clear "Re-authorize" message; 404 on cancel treated as already-gone |
| Webhooks | Webhooks section | None exist for purchases → pollers instead |

## Edge cases handled

Partial receipts & backorders · over-receipt logging · price-deviation
warnings vs PO lines (tolerance %) · MOQ blocking · currency-mismatch
warning (docs define no conversion) · order-unit↔storage-unit conversions
via doc factors · duplicate/idempotent re-pushes (stored `foodics_po_id`,
PUT instead of POST) · transient failures queued in
`foodics.sync.log` with cron replay + manual Retry button · auth failures
surfaced without blocking local operations · soft-deleted upstream records.

## Manual test walkthrough

1. **Foodics Mock → Fake Apps**: create app, redirect URI
   `http://localhost:8069/foodics/oauth/callback`.
2. **Foodics Connection** (from foodics_base): same credentials + redirect;
   Custom API Base URL `http://localhost:8069/foodics_mock/v5`;
   Custom Authorization Base URL `http://localhost:8069/foodics_mock`;
   **PO Creator ID** `seed-user-1`. Click *Authorize with Foodics*.
3. On the connection: *Sync Branches*, *Sync Suppliers*, *Sync Inventory Items*
   (seeds include Tomatoes: box, factor 2, MOQ 10).
4. Map Tomatoes to an Odoo storable product (or enable *Auto-create products*).
5. Create an RFQ for vendor Hyper Market: line qty ≥ 10 (MOQ blocks below),
   price 90 → Confirm → check the **Foodics tab**, then
   *Foodics Mock → Received Purchase Orders*.
6. Receive partially (4 of 10) → mock gets 8 storage units @45, upstream PO →
   *Partially Received*; validate backorder → upstream PO → *Closed*.
7. Return 1 box from the receipt → mock gets a type-4 transaction.
8. Cancel another confirmed PO → its mock record is deleted.
9. Break the API URL temporarily to see the retry queue, restore, hit ↻.

## Go-live checklist

1. Clear both *Custom … Base URL* fields.
2. Set environment + real Client ID/Secret + public Redirect URI.
3. Ask Foodics to grant scopes: `inventory.settings.read/write`,
   `inventory.transactions.read/write`, `general.read`.
4. Fill **PO Creator ID** with a real Foodics user id.

## Flagged ambiguities (documented decisions, not guesses)

- **Foodics actor user ids** required on create (`creator`) but docs define
  no app-acting-as-user convention → configured per connection.
- **TX items carry only quantity+cost** (no unit field) → sent in storage
  units; costs divided by factor so line totals stay identical.
- **Multi-currency** undefined in docs → mismatch shown as warning.
- **Unit strings unstandardized** (“box”, “Kg”) → alias-based UoM guessing,
  manual override field per item.
- **Receipts posted as Closed(4)** so levels move immediately; draft/pending
  TX workflow would need console approval first.
- **additional_cost** has no native Odoo PO equivalent → pass-through field.
