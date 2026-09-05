import json
import uuid

from odoo import http
from odoo.http import request, Response

from . import sample_data


def _json_response(data, status=200):
    return Response(
        json.dumps(data),
        status=status,
        content_type='application/json; charset=utf-8',
    )


def _get_body(req):
    try:
        return json.loads(req.httprequest.data or b'{}')
    except ValueError:
        return {}


def _find_app_by_token(req):
    auth = req.httprequest.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth.split(' ', 1)[1].strip()
    return request.env['foodics.mock.app'].sudo().search([('access_token', '=', token)], limit=1)


class FoodicsMockController(http.Controller):

    # -----------------------------------------------------------
    # OAuth2 "Authorization Code" flow (mirrors console.foodics.com)
    # -----------------------------------------------------------
    @http.route('/foodics_mock/authorize', type='http', auth='user', website=False, csrf=False)
    def authorize(self, client_id=None, state=None, **kwargs):
        app = request.env['foodics.mock.app'].sudo().search([('client_id', '=', client_id)], limit=1)
        if not app:
            return '<h3>Unknown client_id. Create a Foodics Mock App record first ' \
                   '(Foodics Mock &gt; Fake Apps).</h3>'
        return f"""
            <html><body style="font-family: sans-serif; text-align:center; margin-top: 60px;">
                <h2>FOODICS (mock)</h2>
                <p><b>{app.name}</b> is requesting permission to access account
                   <b>{app.business_reference}</b>.</p>
                <form method="post" action="/foodics_mock/authorize/confirm">
                    <input type="hidden" name="client_id" value="{client_id}"/>
                    <input type="hidden" name="state" value="{state or ''}"/>
                    <input type="hidden" name="csrf_token" value="{request.csrf_token()}"/>
                    <button type="submit" name="decision" value="allow"
                            style="background:#5b2eff;color:white;border:0;padding:10px 20px;border-radius:6px;">
                        Authorize
                    </button>
                    <button type="submit" name="decision" value="deny"
                            style="margin-left:10px;padding:10px 20px;border-radius:6px;">
                        Cancel
                    </button>
                </form>
            </body></html>
        """

    @http.route('/foodics_mock/authorize/confirm', type='http', auth='user', website=False, csrf=True)
    def authorize_confirm(self, client_id=None, state=None, decision=None, **kwargs):
        app = request.env['foodics.mock.app'].sudo().search([('client_id', '=', client_id)], limit=1)
        if not app:
            return '<h3>Unknown client_id.</h3>'
        if decision != 'allow':
            sep = '&' if '?' in app.redirect_uri else '?'
            return request.redirect(f'{app.redirect_uri}{sep}error=access_denied&state={state or ""}')
        code = app._issue_code()
        sep = '&' if '?' in app.redirect_uri else '?'
        return request.redirect(f'{app.redirect_uri}{sep}code={code}&state={state or ""}')

    @http.route('/foodics_mock/oauth/token', type='http', auth='public', website=False, csrf=False, methods=['POST'])
    def oauth_token(self, **kwargs):
        body = _get_body(request)
        app = request.env['foodics.mock.app'].sudo().search([
            ('client_id', '=', body.get('client_id')),
            ('client_secret', '=', body.get('client_secret')),
        ], limit=1)
        if not app:
            return _json_response({'message': 'Invalid client_id or client_secret'}, status=401)
        if body.get('grant_type') != 'authorization_code':
            return _json_response({'message': 'Unsupported grant_type'}, status=400)
        if not app.pending_code or app.pending_code != body.get('code'):
            return _json_response({'message': 'Invalid or expired authorization code'}, status=400)

        token = app._issue_token()
        return _json_response({'token_type': 'Bearer', 'access_token': token})

    @http.route('/foodics_mock/v5/tokens/revoke', type='http', auth='public', website=False, csrf=False,
                methods=['DELETE'])
    def revoke_token(self, **kwargs):
        app = _find_app_by_token(request)
        if app:
            app.action_revoke()
        return _json_response({'message': 'Token revoked'})

    # -----------------------------------------------------------
    # Read-only sample data endpoints
    # -----------------------------------------------------------
    @http.route('/foodics_mock/v5/whoami', type='http', auth='public', website=False, csrf=False)
    def whoami(self, **kwargs):
        app = _find_app_by_token(request)
        if not app:
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.whoami_data(app))

    @http.route('/foodics_mock/v5/settings', type='http', auth='public', website=False, csrf=False)
    def settings(self, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.settings_data())

    @http.route('/foodics_mock/v5/branches', type='http', auth='public', website=False, csrf=False)
    def branches(self, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.branches_data())

    @http.route('/foodics_mock/v5/categories', type='http', auth='public', website=False, csrf=False)
    def categories(self, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.categories_data())

    @http.route('/foodics_mock/v5/products', type='http', auth='public', website=False, csrf=False)
    def products(self, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.products_data())

    # -----------------------------------------------------------
    # Accounting master data (taxes, payment methods) + read-only orders,
    # for foodics_accounting's Sync Taxes / Sync Payment Methods / Pull
    # Orders buttons to have something realistic to fetch.
    # -----------------------------------------------------------
    @http.route('/foodics_mock/v5/taxes', type='http', auth='public', website=False, csrf=False)
    def taxes(self, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.taxes_data())

    @http.route('/foodics_mock/v5/payment_methods', type='http', auth='public', website=False, csrf=False)
    def payment_methods(self, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        return _json_response(sample_data.payment_methods_data())

    @http.route('/foodics_mock/v5/orders', type='http', auth='public', website=False, csrf=False, methods=['GET'])
    def list_orders(self, **kwargs):
        app = _find_app_by_token(request)
        if not app:
            return _json_response({'message': 'Unauthenticated'}, status=401)
        status_filter = kwargs.get('filter[status]')
        body = sample_data.orders_data(status_filter)
        domain = [('app_id', '=', app.id)]
        if status_filter:
            # No filter[status] at all means "any status", same as the real /orders endpoint -
            # only narrow the domain when a filter was actually given.
            wanted = [int(s) for s in str(status_filter).split(',') if s.strip().isdigit()]
            domain.append(('status', 'in', wanted))
        mock_pos_orders = request.env['foodics.mock.pos.order'].sudo().search(domain)
        extra = [o._to_payload() for o in mock_pos_orders]
        body['data'] = body['data'] + extra
        body['meta']['total'] = len(body['data'])
        return _json_response(body)

    # -----------------------------------------------------------
    # Orders (pushed from Odoo via foodics_order_sync)
    # -----------------------------------------------------------
    @http.route('/foodics_mock/v5/orders', type='http', auth='public', website=False, csrf=False, methods=['POST'])
    def create_order(self, **kwargs):
        app = _find_app_by_token(request)
        if not app:
            return _json_response({'message': 'Unauthenticated'}, status=401)
        payload = _get_body(request)

        order_id = payload.get('id') or str(uuid.uuid4())
        if request.env['foodics.mock.order'].sudo().search([('foodics_id', '=', order_id)], limit=1):
            return _json_response(
                {'message': 'The given data was invalid.', 'errors': {'id': ['Duplicate ID.']}}, status=422)

        total_price = 0.0
        products_out = []
        for p in payload.get('products', []):
            qty = p.get('quantity', 1)
            unit_price = p.get('unit_price', 0)
            line_total = qty * unit_price
            total_price += line_total
            products_out.append({**p, 'total_price': line_total})

        request.env['foodics.mock.order'].sudo().create({
            'app_id': app.id,
            'foodics_id': order_id,
            'branch_id': payload.get('branch_id'),
            'order_type': payload.get('type', 1),
            'total_price': total_price,
            'raw_payload': json.dumps(payload, indent=2),
            'status': 1,
            'state': 'pending',
        })

        response = dict(payload)
        response.update({
            'id': order_id,
            'products': products_out,
            'subtotal_price': total_price,
            'rounding_amount': 0,
            'total_price': total_price,
            'status': 1,
        })
        return _json_response({'data': response}, status=201)

    @http.route('/foodics_mock/v5/orders_calculator', type='http', auth='public', website=False, csrf=False,
                methods=['POST'])
    def orders_calculator(self, **kwargs):
        """Simplified calculator: sums quantity * unit_price for products/options.
        Does NOT replicate real Foodics tax/discount math - it's only meant to
        let you exercise the request/response wiring before going live.
        """
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        payload = _get_body(request)

        subtotal = 0.0
        for p in payload.get('products', []):
            line = p.get('quantity', 1) * p.get('unit_price', 0)
            for opt in p.get('options', []):
                line += p.get('quantity', 1) * opt.get('quantity', 1) * opt.get('unit_price', 0)
            subtotal += line

        response = dict(payload)
        response.update({
            'subtotal_price': subtotal,
            'rounding_amount': 0,
            'total_price': subtotal,
        })
        return _json_response(response)

    @http.route('/foodics_mock/v5/orders/<string:order_id>', type='http', auth='public', website=False, csrf=False)
    def get_order(self, order_id, **kwargs):
        if not _find_app_by_token(request):
            return _json_response({'message': 'Unauthenticated'}, status=401)
        mock_pos_order = request.env['foodics.mock.pos.order'].sudo().search([('foodics_id', '=', order_id)], limit=1)
        if mock_pos_order:
            return _json_response({'data': mock_pos_order._to_payload()})
        mock_order = request.env['foodics.mock.order'].sudo().search([('foodics_id', '=', order_id)], limit=1)
        if not mock_order:
            return _json_response({'message': 'Not found'}, status=404)
        return _json_response({
            'data': {
                'id': mock_order.foodics_id,
                'branch_id': mock_order.branch_id,
                'type': mock_order.order_type,
                'total_price': mock_order.total_price,
                # Real Foodics reports this as the numeric order.status, not
                # a string - foodics_order_sync's FOODICS_STATUS_TO_STATE
                # mapping expects an int here.
                'status': mock_order.status,
            }
        })
