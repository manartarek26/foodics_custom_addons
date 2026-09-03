import json
import logging
import time

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_PAGES = 200          # safety cap while walking doc-style pagination
RATE_LIMIT_BACKOFF = [1, 2, 4]   # seconds, docs code 429 "Too many attempts"
HTTP_TIMEOUT = 20


# ---------------------------------------------------------------------------
# Typed API exceptions (docs "Errors" section: 200/401/403/404/422/429/500/503)
# ---------------------------------------------------------------------------
class FoodicsApiException(Exception):
    def __init__(self, message, http_status=None, response_payload=None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.response_payload = response_payload


class FoodicsAuthError(FoodicsApiException):
    """401 - No valid Access Token was given."""


class FoodicsPermissionError(FoodicsApiException):
    """403 - Missing scope / no permission (docs Scopes section)."""


class FoodicsNotFoundError(FoodicsApiException):
    """404 - Resource does not exist (e.g. deleted upstream)."""


class FoodicsValidationError(FoodicsApiException):
    """422 - Payload problems; carries the parsed docs' errors{} map."""

    def __init__(self, message, errors=None, **kw):
        super().__init__(message, **kw)
        self.errors = errors or {}

    def human_message(self):
        parts = []
        for field, msgs in self.errors.items():
            joined = '; '.join(msgs if isinstance(msgs, list) else [str(msgs)])
            parts.append(f'{field}: {joined}')
        return _('Foodics rejected the data (%s).') % (' | '.join(parts) or self.message)


class FoodicsTransientError(FoodicsApiException):
    """429 (after in-call backoff), 500, 503 and network failures -
    safe to queue for a later automatic retry."""


RETRYABLE_EXCEPTIONS = (FoodicsTransientError, requests.RequestException)


class FoodicsApi(models.AbstractModel):
    """Single seam between Odoo purchase logic and the Foodics HTTP API.

    Everything purchase-related goes through this adapter so that switching
    from foodics_mock (+ foodics_mock_purchase_ext) to the real Foodics API
    is purely a matter of clearing the Custom Base URL fields on the
    foodics.config record - no code changes here or anywhere else.

    Uses only the existing foodics.config helpers (_api_base/_headers);
    implements GET/POST/PUT/DELETE itself because PUT & DELETE are required
    by the documented Update/Delete endpoints.
    """
    _name = 'foodics.api'
    _description = 'Foodics API Adapter (Purchasing)'

    # Docs "Purchase Order > Statuses"
    PO_STATUS_DRAFT = 1
    PO_STATUS_PENDING = 2
    PO_STATUS_APPROVED = 3
    PO_STATUS_DECLINED = 4
    PO_STATUS_PARTIAL = 5
    PO_STATUS_CLOSED = 6

    # Docs "Inventory Transactions > Types"
    TX_TYPE_PURCHASING = 1
    TX_TYPE_TRANSFER_SENDING = 2
    TX_TYPE_QUANTITY_ADJUSTMENT = 3
    TX_TYPE_RETURN_TO_SUPPLIER = 4
    TX_TYPE_PRODUCTION = 5
    TX_TYPE_TRANSFER_RECEIVING = 6
    TX_TYPE_CONSUMPTION_PRODUCTION = 7
    TX_TYPE_CONSUMPTION_ORDER = 8
    TX_TYPE_RETURN_ORDER = 9
    TX_TYPE_WASTE_ORDER = 10
    TX_TYPE_RETURN_TRANSFER = 11
    TX_TYPE_WASTE_PRODUCTION = 12

    # Docs "Inventory Transactions > Statuses"
    TX_STATUS_DRAFT = 1
    TX_STATUS_PENDING = 2
    TX_STATUS_DECLINED = 3
    TX_STATUS_CLOSED = 4

    # ------------------------------------------------------------------
    # connection helpers
    # ------------------------------------------------------------------
    def _get_config(self, config=None):
        config = config or self.env['foodics.config'].search([], limit=1)
        if not config:
            raise UserError(_(
                'No Foodics connection found. Create one under Foodics > Connection.'))
        if not config.access_token:
            raise UserError(_(
                'The Foodics connection "%s" is not authorized yet. '
                'Open it and click "Authorize with Foodics".') % config.name)
        return config

    # ------------------------------------------------------------------
    # low level HTTP
    # ------------------------------------------------------------------
    def _http(self, config, method, path, params=None, json_body=None):
        """One logical call (with internal backoff per the Errors doc:
        429 "Too many attempts", plus network failures).

        Raises typed Foodics* exceptions; never queues anything itself -
        queueing is the caller's job through foodics.sync.log.
        """
        url = f'{config._api_base()}{path}'
        headers = config._headers()
        response = None
        last_network_exc = None
        attempts = len(RATE_LIMIT_BACKOFF) + 1
        for attempt in range(attempts):
            try:
                response = requests.request(
                    method, url, headers=headers, params=params,
                    json=json_body, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                last_network_exc = exc
                response = None
                if attempt < attempts - 1:
                    time.sleep(RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)])
                continue
            if response.status_code == 429 and attempt < attempts - 1:
                time.sleep(RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)])
                continue
            break
        if response is None:
            raise FoodicsTransientError(
                _('Network failure calling Foodics %s %s: %s')
                % (method, path, last_network_exc))
        if response.status_code in (200, 201):
            try:
                return response.json()
            except ValueError:
                return {}
        self._raise_for_status(response, method, path)

    def _raise_for_status(self, resp, method, path):
        body = None
        try:
            body = resp.json()
        except ValueError:
            pass
        # Always keep a readable excerpt of the RAW reply - non-JSON bodies
        # (HTML error pages, plain text) used to vanish and made failures
        # like HTTP 400 impossible to diagnose.
        raw = (resp.text or '').strip()[:500]
        if body is not None and isinstance(body, dict) and body.get('message'):
            message = str(body['message'])
        else:
            message = f'{resp.status_code} on {method} {path} | raw reply: {raw or "-"}'
        common = {
            'http_status': resp.status_code,
            'response_payload': (json.dumps(body)[:8000] if body is not None else raw),
        }
        if resp.status_code == 401:
            raise FoodicsAuthError(
                _('Foodics rejected the access token (401). Re-authorize the '
                  'connection under Foodics > Connection.'), **common)
        if resp.status_code == 403:
            raise FoodicsPermissionError(
                _('Missing Foodics scope/permission (403): %s') % message, **common)
        if resp.status_code == 404:
            raise FoodicsNotFoundError(_('Not found on Foodics (404): %s') % path, **common)
        if resp.status_code == 422:
            errors = (body or {}).get('errors') or {}
            raise FoodicsValidationError(message, errors=errors, **common)
        if resp.status_code == 400:
            # Not in the docs' table but a permanent client/payload error -
            # never queue it for retry; surface the body for diagnosis.
            raise FoodicsValidationError(
                _('Bad request from Foodics (400): %s | body: %s')
                % (message, raw or '-'),
                errors={}, **common)
        if resp.status_code in (429, 500, 503):
            raise FoodicsTransientError(
                _('Foodics temporarily unavailable (%s): %s')
                % (resp.status_code, message), **common)
        raise FoodicsTransientError(
            _('Unexpected Foodics response (%s): %s') % (resp.status_code, message),
            **common)

    # ------------------------------------------------------------------
    # pagination (docs Pagination section: 50/page, meta.last_page)
    # ------------------------------------------------------------------
    def _paged_get(self, config, path, params=None):
        """Yield every object of a list endpoint, following meta.last_page."""
        params = dict(params or {})
        page = 1
        while page <= MAX_PAGES:
            params['page'] = page
            result = self._http(config, 'GET', path, params=params)
            data = result.get('data') or []
            for item in data:
                yield item
            meta = result.get('meta') or {}
            last_page = meta.get('last_page') or (page if data else page - 1) or 1
            if page >= int(last_page) or not data:
                return
            page += 1

    # ------------------------------------------------------------------
    # queue-aware execution (used for pushes and their retries)
    # ------------------------------------------------------------------
    def execute(self, entry):
        """Run the HTTP exchange described by a foodics.sync.log entry.

        Marks the entry done/error, schedules retries for transient
        problems and re-raises typed exceptions so callers can update local
        records accordingly.
        """
        entry.ensure_one()
        config = self._get_config(entry.config_id)
        entry.write({'last_attempt': fields.Datetime.now(),
                     'request_payload': entry.payload_json})
        try:
            payload = json.loads(entry.payload_json or '{}')
            params = json.loads(entry.params_json or '{}')
            result = self._http(config, entry.method, entry.endpoint,
                                params=params or None, json_body=payload or None)
        except RETRYABLE_EXCEPTIONS as exc:
            entry._mark_transient(str(exc))
            raise
        except FoodicsValidationError as exc:
            entry._mark_error(exc.human_message(), exc.http_status, exc.response_payload,
                              retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload,
                              retryable=False)
            raise
        except FoodicsNotFoundError as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload,
                              retryable=False)
            raise
        entry.write({
            'state': 'done',
            'response_payload': json.dumps(result)[:16000],
            'error_message': False,
        })
        return result

    # ------------------------------------------------------------------
    # pull wrappers (master data / polls) - one audit row per operation
    # ------------------------------------------------------------------
    def _log_pull(self, config, operation):
        return self.env['foodics.sync.log'].create({
            'config_id': config.id,
            'direction': 'pull',
            'operation': operation,
            'method': 'GET',
            'endpoint': '/%s' % operation.replace('list_', '').replace('poll_', ''),
            'state': 'pending',
        })

    def fetch_suppliers(self, config, extra_params=None):
        entry = self._log_pull(config, 'list_suppliers')
        try:
            rows = list(self._paged_get(config, '/suppliers', extra_params))
        except FoodicsValidationError as exc:
            entry._mark_error(exc.human_message(), exc.http_status,
                              exc.response_payload, retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload, retryable=False)
            raise
        except RETRYABLE_EXCEPTIONS as exc:
            entry._mark_transient(str(exc))
            raise
        entry.write({'state': 'done',
                     'response_payload': '%d supplier(s)' % len(rows)})
        return rows

    def fetch_inventory_items(self, config, extra_params=None):
        entry = self._log_pull(config, 'list_inventory_items')
        try:
            rows = list(self._paged_get(config, '/inventory_items', extra_params))
        except FoodicsValidationError as exc:
            entry._mark_error(exc.human_message(), exc.http_status,
                              exc.response_payload, retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload, retryable=False)
            raise
        except RETRYABLE_EXCEPTIONS as exc:
            entry._mark_transient(str(exc))
            raise
        entry.write({'state': 'done',
                     'response_payload': '%d item(s)' % len(rows)})
        return rows

    def fetch_purchase_orders(self, config, extra_params=None):
        entry = self._log_pull(config, 'poll_purchase_orders')
        try:
            rows = list(self._paged_get(config, '/purchase_orders', extra_params))
        except FoodicsValidationError as exc:
            entry._mark_error(exc.human_message(), exc.http_status,
                              exc.response_payload, retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload, retryable=False)
            raise
        except RETRYABLE_EXCEPTIONS as exc:
            entry._mark_transient(str(exc))
            raise
        entry.write({'state': 'done',
                     'response_payload': '%d order(s)' % len(rows)})
        return rows

    def fetch_cost_adjustments(self, config, extra_params=None):
        entry = self._log_pull(config, 'poll_cost_adjustments')
        try:
            rows = list(self._paged_get(
                config, '/cost_adjustment_transactions', extra_params))
        except FoodicsValidationError as exc:
            entry._mark_error(exc.human_message(), exc.http_status,
                              exc.response_payload, retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload, retryable=False)
            raise
        except RETRYABLE_EXCEPTIONS as exc:
            entry._mark_transient(str(exc))
            raise
        entry.write({'state': 'done',
                     'response_payload': '%d adjustment(s)' % len(rows)})
        return rows

    def fetch_inventory_levels(self, config, branch_foodics_id):
        """Docs: GET /inventory_levels/{branch_id} -> rows shaped
        {"pivot": {"quantity", "cost_per_unit"}, "id": <inventory item id>}"""
        entry = self._log_pull(config, 'list_inventory_levels')
        entry.endpoint = '/inventory_levels/%s' % branch_foodics_id
        try:
            result = self._http(config, 'GET', '/inventory_levels/%s' % branch_foodics_id)
        except FoodicsValidationError as exc:
            entry._mark_error(exc.human_message(), exc.http_status,
                              exc.response_payload, retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            entry._mark_error(str(exc), exc.http_status, exc.response_payload, retryable=False)
            raise
        except RETRYABLE_EXCEPTIONS as exc:
            entry._mark_transient(str(exc))
            raise
        entry.write({'state': 'done',
                     'response_payload': json.dumps(result)[:4000]})
        return result.get('data') or []

    def get_purchase_order(self, config, foodics_po_id, log_operation=True):
        entry = None
        if log_operation:
            entry = self._log_pull(config, 'get_purchase_order')
            entry.endpoint = '/purchase_orders/%s' % foodics_po_id
        try:
            result = self._http(config, 'GET', '/purchase_orders/%s' % foodics_po_id)
        except FoodicsValidationError as exc:
            if entry:
                entry._mark_error(exc.human_message(), exc.http_status,
                                  exc.response_payload, retryable=False)
            raise
        except (FoodicsAuthError, FoodicsPermissionError) as exc:
            if entry:
                entry._mark_error(str(exc), exc.http_status, exc.response_payload,
                                  retryable=False)
            raise
        except RETRYABLE_EXCEPTIONS as exc:
            if entry:
                entry._mark_transient(str(exc))
            raise
        if entry:
            entry.write({'state': 'done',
                         'response_payload': json.dumps(result)[:8000]})
        return result.get('data') or {}
