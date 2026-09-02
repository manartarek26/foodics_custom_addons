{
    'name': 'Foodics Mock Server (Testing Only)',
    'version': '18.0.1.0.0',
    'summary': 'Simulates the Foodics API/OAuth endpoints so foodics_base/foodics_order_sync can '
               'be tested without real Foodics credentials',
    'description': """
Foodics Mock Server
=====================
Not a real integration - a stand-in for it.

1. Install this module.
2. Go to Foodics Mock > Fake Apps and create a record with a Client ID,
   Client Secret and Redirect URI of your choosing (the Redirect URI must
   match what you put on your foodics.config record in foodics_base,
   e.g. http://localhost:8069/foodics/oauth/callback).
3. On your Foodics Connection (foodics_base), fill in the same
   Client ID / Secret / Redirect URI, and set:
     Custom API Base URL:          http://localhost:8069/foodics_mock/v5
     Custom Authorization Base URL: http://localhost:8069/foodics_mock
4. Click "Authorize with Foodics" - you'll go through a fake but structurally
   identical login/consent screen, and land back with a real access token
   issued by this mock server.
5. Test "Fetch Business Settings", "Sync Branches", "Sync Menu" and
   "Send to Foodics" from a Sale Order - all against canned sample data
   shaped like Foodics' real API docs.
6. Optional: to test webhooks (and the reconciliation cron in
   foodics_order_sync) without waiting for a real Foodics account, copy the
   "Webhook URL" shown on the Foodics Connection record (foodics_base) into
   this Fake App's "Webhook URL" field, then use the "Simulate: Accepted /
   Closed / Declined" buttons on a Received Order. "Simulate Missed
   Webhook" changes the status WITHOUT calling the webhook, so you can
   confirm the reconciliation cron catches up on its own.
7. For foodics_accounting specifically: Foodics Mock > Mock POS Orders lets
   you build a POS sale by hand (products, taxes, an optional charge, split
   payments, an optional customer) and fire it at the same Webhook URL as
   `order.created`/`order.updated`/`customer.order.*` - the exact events
   foodics_accounting listens for. "Return This Order" rings up a genuine
   second order at status Returned, the way Foodics itself models a return.
   These orders are also served back over GET /orders and GET /orders/<id>,
   so "Pull Orders" sees the same data the webhook would have sent.

This module has no dependency on foodics_base, foodics_order_sync or
foodics_accounting (and none of them have any on it either) - it only
talks to them at runtime, through plain HTTP/webhooks, exactly like the
real Foodics would. That is deliberate: once your real Foodics credentials
arrive, just uninstall this module, clear the two "Custom ... Base URL"
fields on the Foodics Connection, and put in the real Client ID/Secret -
nothing else changes.
""",
    'category': 'Sales/Point of Sale',
    'author': 'Custom',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/foodics_mock_app_views.xml',
        'views/foodics_mock_order_views.xml',
        'views/foodics_mock_pos_order_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
