{
    'name': 'Foodics Base',
    'version': '18.0.1.0.0',
    'summary': 'Shared foundation for any Foodics integration: connection/auth, branches, '
               'product mapping & approval, webhook intake',
    'description': """
Foodics Base
============
Not tied to Sales specifically - this is the shared foundation any Foodics
integration (order sync, accounting, purchase, inventory, ...) builds on:

- Foodics Connection: OAuth2 "Authorization Code" flow, environment
  (sandbox/production), and generic authenticated GET/POST/DELETE helpers
  with automatic pagination.
- Foodics Branches, with a manual mapping to an Odoo Warehouse (and, derived
  from that, a Company). See the module documentation (docs/base_and_mock.html
  at the root of this repo) for why that mapping is manual and how the rest
  of the integration uses it.
- Foodics Product Mapping: every product pulled from a Foodics menu sync
  lands here first. Depending on the "Foodics" settings checkbox
  (Settings > General Settings), it either auto-creates/links the matching
  Odoo product right away, or waits in a review queue for a human to
  approve it. Either way, the resulting product is flagged
  "From Foodics" so it is always obvious where it came from, and a basic
  duplicate check (by internal reference / barcode) avoids creating a
  second product for something that already exists in Odoo.
- A generic webhook intake endpoint + log (Foodics Webhook Log) that any
  other Foodics module can hook into by inheriting the model and adding a
  `_handle_<event_name_with_underscores>` method - no changes needed here
  when a new integration wants to react to a new Foodics webhook event.

This module talks to whatever base URL is configured on the connection -
point it at the real Foodics API/console, or at the companion
foodics_mock module while you wait for real credentials.
""",
    'category': 'Sales/Point of Sale',
    'author': 'Custom',
    'depends': ['base', 'stock'],
    'data': [
        'security/ir.model.access.csv',
        'views/foodics_config_views.xml',
        'views/foodics_branch_views.xml',
        'views/foodics_product_mapping_views.xml',
        'views/foodics_webhook_log_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
