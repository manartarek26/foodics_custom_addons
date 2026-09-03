import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 6
RETRY_BASE_DELAY_SECONDS = 60  # doubled on every attempt, capped at 1h


class FoodicsSyncLog(models.Model):
    """Audit trail + retry queue for every purchase-side exchange with the
    Foodics API.

    One row per logical operation. Push operations (create/update/delete
    POs, receiving & return transactions) that fail with a transient error
    stay here as `pending_retry` and are replayed automatically by the
    cron (see data/ir_cron_data.xml) until they succeed or exhaust
    MAX_RETRY_ATTEMPTS. When a queued push finally succeeds,
    _apply_success() hands the response back to the owning record so its
    side effects are never lost (idempotent handlers).
    """
    _name = 'foodics.sync.log'
    _description = 'Foodics Purchase Sync Log / Retry Queue'
    _order = 'create_date desc'
    _rec_name = 'operation'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade')
    direction = fields.Selection([('push', 'Push to Foodics'), ('pull', 'Pull from Foodics')],
                                 required=True, default='push')
    operation = fields.Char(required=True, help="e.g. create_po, post_receipt_tx, "
                           "list_suppliers")
    res_model = fields.Char()
    res_id = fields.Integer(index=True)
    foodics_id = fields.Char(string='Foodics Object ID')
    method = fields.Selection([('GET', 'GET'), ('POST', 'POST'), ('PUT', 'PUT'),
                               ('DELETE', 'DELETE')], default='POST', required=True)
    endpoint = fields.Char(required=True)
    params_json = fields.Text(copy=False)
    payload_json = fields.Text(copy=False)
    request_payload = fields.Text(readonly=True)
    response_payload = fields.Text(readonly=True)
    http_status = fields.Integer(readonly=True)
    state = fields.Selection([
        ('done', 'Done'),
        ('error', 'Failed'),
        ('failed', 'Permanently Failed'),
        ('warning', 'Warning'),
        ('pending', 'Pending'),
    ], default='pending', index=True, required=True)
    retryable = fields.Boolean(default=False, help='Transient failure - eligible for '
                               'automatic retries.')
    error_message = fields.Text()
    retry_count = fields.Integer(default=0, copy=False)
    next_retry = fields.Datetime(copy=False)
    last_attempt = fields.Datetime(copy=False)

    def _mark_done(self, response_text=''):
        self.write({'state': 'done', 'response_payload': response_text[:16000],
                    'error_message': False})

    def _mark_error(self, message, http_status=None, response_payload=None, retryable=False):
        self.ensure_one()
        self.write({
            'state': 'error',
            'error_message': message[:4000],
            'http_status': http_status or 0,
            'response_payload': (response_payload or '')[:16000],
            'retryable': False,  # permanent classes are never auto-retried
            'next_retry': False,
        })

    def _mark_transient(self, message):
        """Called by the adapter for network/5xx/429-exhausted failures:
        schedule an exponential-backoff retry."""
        self.ensure_one()
        next_delay = min(RETRY_BASE_DELAY_SECONDS * (2 ** self.retry_count), 3600)
        self.write({
            'state': 'error',
            'retryable': True,
            'retry_count': self.retry_count + 1,
            'next_retry': fields.Datetime.add(fields.Datetime.now(), seconds=next_delay),
            'error_message': message[:4000],
        })

    def _mark_warning(self, message, response_payload=None):
        self.write({'state': 'warning', 'error_message': message[:4000],
                    'response_payload': (response_payload or '')[:16000]})

    # ------------------------------------------------------------------
    # manual retry button (also used by the cron for one entry at a time)
    # ------------------------------------------------------------------
    def action_retry(self):
        """Replay this queued exchange; on success hand the result to the
        owning record's idempotent _foodics_apply_* handler."""
        api = self.env['foodics.api']
        done_ids = []
        for entry in self.filtered(lambda e: e.state in ('error', 'pending')):
            try:
                result = api.execute(entry)
            except Exception:
                continue  # execute() already recorded the failure details
            entry._apply_success(result)
            done_ids.append(entry.id)
        return done_ids

    def _cron_retry_pending(self):
        """Cron worker: replay due, retryable push operations."""
        now = fields.Datetime.now()
        due = self.search([
            ('state', '=', 'error'),
            ('retryable', '=', True),
            ('direction', '=', 'push'),
            ('next_retry', '<=', now),
            ('retry_count', '<', MAX_RETRY_ATTEMPTS),
        ], limit=25)
        for entry in due.with_context(foodics_from_cron=True):
            try:
                result = self.env['foodics.api'].execute(entry)
                entry._apply_success(result)
            except Exception:
                _logger.warning('Foodics sync retry failed for log #%s', entry.id,
                                exc_info=True)
        # give up permanently after too many attempts
        exhausted = self.search([
            ('state', '=', 'error'), ('retryable', '=', True),
            ('retry_count', '>=', MAX_RETRY_ATTEMPTS)])
        if exhausted:
            exhausted.write({'state': 'failed', 'retryable': False})

    def _apply_success(self, data):
        """Dispatch the successful response to the model that owns this
        operation (e.g. purchase.order._foodics_apply_create_po). Handlers
        must be idempotent because a queued op can succeed long after it
        was first attempted.

        A handler crashing on an unexpected response shape must never look
        like a success - the entry is flipped back to a permanent error
        with the reason instead."""
        for entry in self:
            if not entry.res_model:
                continue
            handler_name = '_foodics_apply_%s' % entry.operation
            target_model = self.env[entry.res_model]
            if not hasattr(target_model, handler_name):
                continue
            records = target_model.browse(entry.res_id) if entry.res_id else target_model
            if entry.res_id and not records.exists():
                continue
            try:
                getattr(records.with_context(foodics_queued_success=True),
                        handler_name)(entry, data)
            except Exception as exc:
                _logger.exception('Foodics post-success handling failed for log #%s',
                                  entry.id)
                entry.write({
                    'state': 'error',
                    'retryable': False,
                    'error_message':
                        _('Foodics replied, but applying the result failed: %s')
                        % exc,
                })

    @api.model
    def _mark_warning_standalone(self, config, message):
        """One-off informational warning row (no retry semantics) - used by
        flows that must not block on upstream state, e.g. cancelling an
        order Foodics already closed."""
        return self.create({
            'config_id': config.id,
            'direction': 'pull',
            'operation': 'warning',
            'method': 'GET',
            'endpoint': '-',
            'state': 'warning',
            'error_message': message[:4000],
        })

    @api.model
    def _purge_old_logs(self):
        """Housekeeping: drop pull-audit rows older than 30 days."""
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        self.search([('direction', '=', 'pull'),
                     ('create_date', '<', cutoff)]).unlink()

    def action_open_record(self):
        self.ensure_one()
        if self.res_model and self.res_id:
            return {
                'type': 'ir.actions.act_window',
                'res_model': self.res_model,
                'res_id': self.res_id,
                'view_mode': 'form',
            }
        return {'type': 'ir.actions.act_window_close'}
