{
    'name': 'Foodics Purchasing',
    'version': '18.0.1.0.0',
    'summary': 'Full purchase lifecycle between Odoo 18 and Foodics: '
               'suppliers, inventory items, purchase orders, receiving '
               '(partial/full), returns to supplier and cost updates',
    'description': """
Foodics Purchasing
===================
Odoo-driven purchase integration with Foodics (foodics.com). Odoo is the
source of truth:

- Confirm a Purchase Order in Odoo  -> pushed to Foodics /purchase_orders
  (docs: create allowed in Draft/Pending; Pending needs submitter_id).
- Validate a receipt in Odoo        -> pushed as an Inventory Transaction
  type 1 "Purchasing" referencing the purchase_order_id. Per the Foodics
  ERP guide, only inventory transactions move stock on the Foodics side.
  Partial receipts push one transaction per picking; the upstream PO moves
  through Partially Received (5) -> Closed (6).
- Return goods in Odoo              -> Inventory Transaction type 4
  "Return to Supplier".
- Cancel an order                   -> DELETE (draft) or status update to
  Declined (4) upstream.
- Suppliers & Inventory Items       -> pulled from Foodics into shadow
  models linked to res.partner / product.product for mapping.
- Cost Adjustments + PO statuses    -> polled periodically (Foodics has no
  purchase webhooks) using the documented updated_after filters.

All HTTP goes through a single adapter (models/foodics_api.py). Point the
foodics.config record at foodics_mock (+ foodics_mock_purchase_ext) to test
today; clear the Custom Base URLs at go-live - no code changes needed.

Edge cases handled per docs: pagination (50/page), 401/403/404/422/429/500/
503 error mapping, retry queue with backoff, idempotent re-pushes, minimum
order quantities, unit_to_storage_factor conversions, currency mismatch
warnings and soft-deleted upstream records.
""",
    'category': 'Purchase',
    'author': 'Custom',
    'depends': ['foodics_base', 'purchase_stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        # views/actions first, menus reference their action ids
        'views/foodics_config_views.xml',
        'views/foodics_master_views.xml',
        'views/purchase_order_views.xml',
        'views/stock_picking_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
