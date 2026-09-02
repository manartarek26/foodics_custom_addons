import secrets

from odoo import fields, models


class FoodicsMockApp(models.Model):
    _name = 'foodics.mock.app'
    _description = 'Foodics Mock Developer App (stands in for the real Foodics dev app)'
    _rec_name = 'name'

    name = fields.Char(default='My Test App', required=True)
    client_id = fields.Char(required=True, default=lambda self: secrets.token_hex(8))
    client_secret = fields.Char(required=True, default=lambda self: secrets.token_hex(16))
    redirect_uri = fields.Char(
        required=True,
        help='Must match the Redirect URI configured on the foodics.config record, '
             'e.g. http://localhost:8069/foodics/oauth/callback')

    business_name = fields.Char(default='My Test Restaurant')
    business_reference = fields.Char(default='TESTBIZ001')

    webhook_url = fields.Char(
        help='Paste in the "Webhook URL" shown on the matching Foodics Connection record '
             '(foodics_base), e.g. http://localhost:8069/foodics/webhook/<secret>. Leave empty '
             'to skip firing webhooks entirely - order status changes will then only become '
             'visible in Odoo via the "Refresh Status" button or the reconciliation cron in '
             'foodics_order_sync.')

    # Runtime OAuth state - simplistic, single-session mock, good enough for testing.
    pending_code = fields.Char(copy=False, readonly=True)
    access_token = fields.Char(copy=False, readonly=True)

    def _issue_code(self):
        self.ensure_one()
        code = secrets.token_urlsafe(24)
        self.pending_code = code
        return code

    def _issue_token(self):
        self.ensure_one()
        token = secrets.token_hex(32)
        self.write({'access_token': token, 'pending_code': False})
        return token

    def action_revoke(self):
        self.write({'access_token': False, 'pending_code': False})
