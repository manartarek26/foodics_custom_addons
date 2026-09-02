import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class FoodicsBaseController(http.Controller):

    # ------------------------------------------------------------------
    # OAuth2 "Authorization Code" flow
    # ------------------------------------------------------------------
    @http.route('/foodics/oauth/callback', type='http', auth='user', website=False, csrf=False)
    def foodics_oauth_callback(self, code=None, state=None, **kwargs):
        """Foodics redirects the browser back here with ?code=...&state=...
        after the user authorizes our app. We match the state to the right
        foodics.config record, exchange the code for an access token, and
        show a small confirmation page.
        """
        config = request.env['foodics.config'].sudo().search([('oauth_state', '=', state)], limit=1)
        if not config:
            return '<h3>Could not match this authorization to a Foodics connection ' \
                   '(state mismatch or expired). Please retry from Odoo.</h3>'
        if not code:
            return '<h3>Authorization was cancelled or failed.</h3>'

        config._exchange_code_for_token(code)

        return """
            <html><body style="font-family: sans-serif; text-align:center; margin-top: 80px;">
                <h2>&#9989; Foodics authorization successful</h2>
                <p>You can close this tab and go back to Odoo.</p>
                <script>setTimeout(function(){ window.close(); }, 2000);</script>
            </body></html>
        """

    # ------------------------------------------------------------------
    # Generic webhook intake - see foodics.webhook.log for how a specific
    # event gets handled by whichever integration module cares about it.
    # ------------------------------------------------------------------
    @http.route('/foodics/webhook/<string:secret>', type='http', auth='public', website=False, csrf=False,
                methods=['POST'])
    def foodics_webhook(self, secret, **kwargs):
        config = request.env['foodics.config'].sudo().search([('webhook_secret', '=', secret)], limit=1)
        if not config:
            return request.make_response(
                json.dumps({'message': 'Unknown webhook endpoint'}),
                status=404, headers=[('Content-Type', 'application/json')])

        try:
            payload = json.loads(request.httprequest.data or b'{}')
        except ValueError:
            return request.make_response(
                json.dumps({'message': 'Invalid JSON'}),
                status=400, headers=[('Content-Type', 'application/json')])

        event = payload.get('event') or 'unknown'
        log = request.env['foodics.webhook.log'].sudo().create({
            'config_id': config.id,
            'event': event,
            'raw_payload': json.dumps(payload),
        })
        log.process()

        # Always acknowledge with 200, even if our own handler errored out -
        # that error is recorded on `log` for a human to fix and reprocess.
        # Returning a 4xx/5xx here would just make Foodics retry the same
        # webhook forever, which does not help.
        return request.make_response(
            json.dumps({'received': True}), status=200, headers=[('Content-Type', 'application/json')])
