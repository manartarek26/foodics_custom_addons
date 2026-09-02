{
    'name': 'Foodics Accounting',
    'version': '18.0.1.0.0',
    'summary': 'Turn closed/returned Foodics POS orders into Odoo invoices, credit notes, '
               'payments and journal entries - via webhooks, with a pull cron as the safety net',
    'description': """
Foodics Accounting
===================
Implements Foodics' own "Accounting/ERP Integration" guide on top of
foodics_base: every closed order becomes a real Odoo POS order (invoiced
immediately), every returned order becomes a second POS order with negated
quantities (invoiced as a credit note), and every POS payment becomes a
reconciled Odoo payment - each carrying its Foodics ID for traceability and
each import guarded against duplicates.

- Foodics > Branches: map each Foodics branch to an Odoo POS Point
  (`pos.config`) - the destination POS orders for that branch's sales are
  created into, in a persistent session opened lazily on the first order
  and left open (closing it has no accounting effect here, since every
  order is invoiced individually the moment it's synced - it's only ever
  needed for reporting hygiene, and is entirely optional/manual).
- Foodics > Tax Mapping / Payment Methods: map Foodics taxes to Odoo taxes
  and Foodics payment methods to an Odoo POS payment method (must be one of
  the payment methods enabled on the mapped branch's POS Point) once,
  before syncing orders.
- foodics.pos.order: one record per Foodics order (status 4 "Closed" or 5
  "Returned" - other statuses are not accounting-relevant per Foodics'
  guide). Holds the parsed lines/payments and a link to the Odoo POS order
  and invoice/credit note it produced.
- Closed orders become a real `pos.order`, built from the parsed order
  lines/payments and invoiced immediately (`action_pos_order_invoice()`) -
  so these sales show up in POS reporting with a real reconciled payment,
  not just a bookkeeping-only invoice. Returned orders become a second
  `pos.order` with negated quantities in the same session (no payments
  attached - Foodics doesn't say whether a return paid cash back, was
  exchanged, or was waived, so that stays a manual step); Odoo picks
  `out_refund` automatically from the negative total, and the resulting
  credit note is linked back to the original invoice for traceability.
- A webhook handler for `order.updated` / `order.created` / the
  `customer.order.*` equivalents (registered on top of foodics_base's
  generic webhook intake) creates/updates the matching foodics.pos.order and
  its invoice the moment Foodics reports the change.
- "Foodics: Pull POS Orders" scheduled action is the safety net: it walks
  `GET /orders` (paginated by `reference`, as documented) for anything the
  webhook missed, using the exact same idempotent upsert as the webhook
  path - so running it twice, or reprocessing the same webhook after a
  retry, never creates a duplicate invoice or payment.
- "Foodics: Retry Pending Invoices" scheduled action retries any order left
  in "Needs Review" (e.g. a tax/payment method mapping was missing at the
  time) once a human has fixed the mapping.

Note: a `pos.payment.method` of type "cash" can only be attached to one POS
Point, so a single connection-wide "Foodics Cash -> Odoo Cash" mapping only
works cleanly for a single-branch setup. A multi-branch connection where
several branches take cash needs a dedicated Cash `pos.payment.method` per
branch's POS Point - not handled automatically here.

Depends on foodics_base + point_of_sale - safe to install without
foodics_order_sync, and independent of it (they cover two different
directions: order_sync pushes Odoo sale orders out, this module pulls
Foodics POS sales in as real Odoo POS orders).
""",
    'category': 'Accounting/Accounting',
    'author': 'Custom',
    'depends': ['foodics_base', 'point_of_sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/foodics_tax_mapping_views.xml',
        'views/foodics_payment_method_views.xml',
        'views/foodics_pos_order_views.xml',
        'views/foodics_config_views.xml',
        'views/foodics_branch_views.xml',
        'views/account_move_views.xml',
        'views/menu_views.xml',
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
