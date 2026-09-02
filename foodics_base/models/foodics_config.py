import json
import logging
import secrets

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Real Foodics URLs. If you fill "Custom API/Authorization Base URL" on the
# record, those override these (handy for pointing at foodics_mock).
FOODICS_URLS = {
    'sandbox': {
        'api': 'https://api-sandbox.foodics.com/v5',
        'console': 'https://console-sandbox.foodics.com',
    },
    'production': {
        'api': 'https://api.foodics.com/v5',
        'console': 'https://console.foodics.com',
    },
}


class FoodicsAPIError(UserError):
    """A UserError that also carries the parsed HTTP status code/body, so a
    caller that needs to react to a *specific* Foodics error (e.g. a 422
    "Duplicate ID" when re-sending an order - see foodics_order_sync) can do
    so without re-parsing the human-readable message string.
    """

    def __init__(self, message, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


class FoodicsConfig(models.Model):
    _name = 'foodics.config'
    _description = 'Foodics Connection'

    name = fields.Char(default='Foodics Connection', required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    environment = fields.Selection([
        ('sandbox', 'Sandbox'),
        ('production', 'Production'),
    ], default='sandbox', required=True)

    # Leave these empty to use the real Foodics sandbox/production URLs.
    # Fill them in to point at the foodics_mock test server instead, e.g.
    # http://localhost:8069/foodics_mock/v5 and
    # http://localhost:8069/foodics_mock
    custom_api_base_url = fields.Char(
        string='Custom API Base URL',
        help='Leave empty to use the real Foodics API. '
             'Fill in to test against the foodics_mock module, e.g. '
             'http://localhost:8069/foodics_mock/v5')
    custom_console_base_url = fields.Char(
        string='Custom Authorization Base URL',
        help='Leave empty to use the real Foodics console. '
             'Fill in to test against the foodics_mock module, e.g. '
             'http://localhost:8069/foodics_mock')

    client_id = fields.Char(string='Client ID')
    client_secret = fields.Char(string='Client Secret')
    redirect_uri = fields.Char(
        string='Redirect URI',
        help='Must be reachable from the browser and match the value your '
             'Foodics app was registered with, e.g. '
             'https://your-odoo-domain.com/foodics/oauth/callback')

    oauth_state = fields.Char(copy=False, help='Internal - used to validate the OAuth callback')
    access_token = fields.Char(copy=False)
    token_type = fields.Char(copy=False, default='Bearer')
    is_connected = fields.Boolean(compute='_compute_is_connected')

    webhook_secret = fields.Char(
        copy=False, default=lambda self: secrets.token_urlsafe(24),
        help='Part of the URL below. Regenerate it if you suspect it leaked - '
             'the old webhook URL stops working immediately.')
    webhook_url = fields.Char(
        compute='_compute_webhook_url',
        help='Give this URL to Foodics support as your webhook endpoint. '
             'While testing, paste it into the matching Foodics Mock App\'s '
             '"Webhook URL" field instead.')

    business_name = fields.Char(readonly=True)
    business_reference = fields.Char(readonly=True)
    business_currency = fields.Char(readonly=True)
    business_timezone = fields.Char(readonly=True)
    tax_inclusive_pricing = fields.Boolean(readonly=True)

    branch_count = fields.Integer(compute='_compute_counts')
    product_mapping_count = fields.Integer(compute='_compute_counts')

    @api.depends('access_token')
    def _compute_is_connected(self):
        for rec in self:
            rec.is_connected = bool(rec.access_token)

    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            rec.webhook_url = f'{base}/foodics/webhook/{rec.webhook_secret}' if rec.webhook_secret else False

    def _compute_counts(self):
        for rec in self:
            rec.branch_count = self.env['foodics.branch'].search_count([('config_id', '=', rec.id)])
            rec.product_mapping_count = self.env['foodics.product.mapping'].search_count(
                [('config_id', '=', rec.id)])

    def action_regenerate_webhook_secret(self):
        for rec in self:
            rec.webhook_secret = secrets.token_urlsafe(24)

    # ---------------------------------------------------------------
    # URL / header helpers
    # ---------------------------------------------------------------
    def _api_base(self):
        self.ensure_one()
        return (self.custom_api_base_url or FOODICS_URLS[self.environment]['api']).rstrip('/')

    def _console_base(self):
        self.ensure_one()
        return (self.custom_console_base_url or FOODICS_URLS[self.environment]['console']).rstrip('/')

    def _token_url(self):
        self.ensure_one()
        # The real token endpoint lives at the API root (…/oauth/token), not
        # under /v5. When pointed at the mock server we keep it simple and
        # just append /oauth/token to whatever base URL was configured.
        base = self._api_base()
        root = base[:-3] if base.endswith('/v5') else base
        return f'{root}/oauth/token'

    def _headers(self):
        self.ensure_one()
        if not self.access_token:
            raise UserError(_('This Foodics connection has no access token yet. Please authorize first.'))
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    # ---------------------------------------------------------------
    # OAuth2 Authorization Code flow
    # ---------------------------------------------------------------
    def action_start_authorization(self):
        """Step 1: open Foodics' authorize page in a new tab."""
        self.ensure_one()
        if not self.client_id or not self.redirect_uri:
            raise UserError(_('Please set Client ID and Redirect URI first.'))
        state = secrets.token_urlsafe(16)
        self.oauth_state = state
        url = f'{self._console_base()}/authorize?client_id={self.client_id}&state={state}'
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def _exchange_code_for_token(self, code):
        """Step 2 (called by the controller once Foodics redirects back)."""
        self.ensure_one()
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
        }
        try:
            resp = requests.post(self._token_url(), json=payload, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            _logger.error('Foodics token exchange failed: %s', e)
            raise UserError(_('Failed to get an access token from Foodics: %s') % e)
        data = resp.json()
        self.write({
            'access_token': data.get('access_token'),
            'token_type': data.get('token_type', 'Bearer'),
            'oauth_state': False,
        })
        return True

    def action_revoke_token(self):
        self.ensure_one()
        if not self.access_token:
            return
        try:
            requests.delete(f'{self._api_base()}/tokens/revoke', headers=self._headers(), timeout=20)
        except requests.RequestException as e:
            _logger.warning('Foodics token revoke call failed (clearing token locally anyway): %s', e)
        self.write({'access_token': False})

    # ---------------------------------------------------------------
    # Data fetching / syncing
    # ---------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        data = self._get('/whoami').get('data', {})
        self.business_name = data.get('name') or (data.get('business') or {}).get('name')
        self.business_reference = data.get('reference')
        return self._notify(_('Connected'), _('whoami call succeeded.'))

    def action_fetch_settings(self):
        self.ensure_one()
        data = self._get('/settings').get('data', {})
        self.business_currency = data.get('business_currency')
        self.business_timezone = data.get('business_timezone')
        self.tax_inclusive_pricing = data.get('tax_inclusive_pricing', False)
        return self._notify(_('Settings fetched'), _('Business settings updated.'))

    def action_sync_branches(self):
        self.ensure_one()
        data = self._get_all('/branches')
        created, updated = self.env['foodics.branch']._sync_from_foodics(self, data)
        return self._notify(_('Branches synced'), _('%s created, %s updated.') % (created, updated))

    def action_sync_menu(self):
        self.ensure_one()
        data = self._get_all('/products', params={'include': 'category,tax_group'})
        created, updated = self.env['foodics.product.mapping']._sync_from_foodics(self, data)
        return self._notify(
            _('Menu synced'),
            _('%s new product(s) received (check Foodics > Product Mapping), %s updated.') % (created, updated))

    # ---------------------------------------------------------------
    # Generic HTTP helpers - shared by every Foodics module through the
    # config record; nothing order/branch/product-specific lives here.
    # ---------------------------------------------------------------
    def _request(self, method, path, params=None, json_payload=None):
        self.ensure_one()
        url = f'{self._api_base()}{path}'
        try:
            resp = requests.request(
                method, url, headers=self._headers(), params=params or {},
                data=json.dumps(json_payload) if json_payload is not None else None,
                timeout=20)
        except requests.RequestException as e:
            _logger.error('Foodics %s %s failed: %s', method, url, e)
            raise FoodicsAPIError(_('Could not reach Foodics: %s') % e)

        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}

        if resp.status_code >= 400:
            message = body.get('message') or resp.reason
            _logger.warning('Foodics %s %s returned %s: %s', method, url, resp.status_code, body)
            raise FoodicsAPIError(
                _('Foodics API call failed (%s): %s') % (resp.status_code, message),
                status_code=resp.status_code, body=body)
        return body

    def _get(self, path, params=None):
        return self._request('GET', path, params=params)

    def _post(self, path, payload):
        return self._request('POST', path, json_payload=payload)

    def _delete(self, path):
        return self._request('DELETE', path)

    def _get_all(self, path, params=None):
        """Foodics paginates every List endpoint at 50 records/page (see
        their Pagination docs) - walk every page and return the combined
        `data` list, instead of silently only ever seeing the first 50
        branches/products/whatever.
        """
        self.ensure_one()
        params = dict(params or {})
        params.setdefault('page', 1)
        results = []
        while True:
            body = self._get(path, params=params)
            results.extend(body.get('data', []))
            meta = body.get('meta') or {}
            current_page = meta.get('current_page') or params['page']
            last_page = meta.get('last_page') or current_page
            if current_page >= last_page:
                break
            params['page'] = current_page + 1
        return results

    def _notify(self, title, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': 'success', 'sticky': False},
        }

    # ---------------------------------------------------------------
    # Smart button openers
    # ---------------------------------------------------------------
    def action_open_branches(self):
        return self._open_related('foodics.branch', _('Foodics Branches'))

    def action_open_product_mappings(self):
        return self._open_related('foodics.product.mapping', _('Foodics Product Mapping'))

    def _open_related(self, model, name):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': model,
            'view_mode': 'list,form',
            'domain': [('config_id', '=', self.id)],
            'context': {'default_config_id': self.id},
        }
