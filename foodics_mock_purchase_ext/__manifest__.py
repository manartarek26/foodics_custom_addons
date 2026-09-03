{
    'name': 'Foodics Mock Purchase Endpoints (Testing Only)',
    'version': '18.0.1.0.0',
    'summary': 'Adds the Foodics purchasing endpoints (suppliers, purchase_orders, '
               'inventory_items, inventory_transactions, cost_adjustments, '
               'inventory_levels) to the foodics_mock fake server',
    'description': """
Foodics Mock Purchase Endpoints
================================
A pure ADD-ON to the foodics_mock module. It does not modify any existing
module - it only registers additional routes under the same
/foodics_mock/v5 namespace so that the foodics_purchase module can be
tested end-to-end before real Foodics API access is available.

Endpoints added (all shaped exactly after the Foodics API Docs PDFs):
  - /suppliers                (list/get/create/update/delete/restore + item attach/detach)
  - /inventory_items          (list/get/create/update/delete)
  - /purchase_orders          (list/get/create/update/delete, statuses 1..6,
                               partial receipts via quantity_received)
  - /inventory_transactions   (list/get/create/update/delete, types 1..12)
  - /cost_adjustment_transactions (list)
  - /inventory_levels/{branch_id} (live levels computed from Purchasing /
                               Return-to-Supplier transactions)

State is stored in Odoo models (foodics.mock.*) so status transitions and
partial receiving behave realistically. Sample data matching the doc
examples (Tomatoes in boxes, Hyper Market supplier, PUR-000001...) is
seeded lazily per Fake App on first use.

Install this together with foodics_mock + foodics_purchase for testing.
For go-live simply point the Foodics Connection at the real API and clear
the Custom Base URLs - this module can stay installed but becomes unused.
""",
    'category': 'Sales/Point of Sale',
    'author': 'Custom',
    'depends': ['foodics_mock'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/foodics_mock_purchase_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
