import logging
import time

import requests

from odoo import fields, models

_logger = logging.getLogger(__name__)

# Mirrors the real Foodics numeric order.status values closely enough for
# testing purposes (see the Reservation/Order Statuses tables in Foodics'
# docs). 6 (joined) isn't reachable from here - a mock cashier never merges
# dine-in orders.
STATUS_LABELS = {
    1: 'pending',
    2: 'active',
    3: 'declined',
    4: 'closed',
    5: 'returned',
    7: 'void',
}


class FoodicsMockOrder(models.Model):
    _name = 'foodics.mock.order'
    _description = 'Foodics Mock Order (orders received by the fake API)'
    _rec_name = 'foodics_id'
    _order = 'create_date desc'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade')
    foodics_id = fields.Char(required=True)
    branch_id = fields.Char()
    order_type = fields.Integer()
    total_price = fields.Float()
    raw_payload = fields.Text()
    status = fields.Integer(default=1, string='Status (numeric)')
    state = fields.Char(default='pending')

    # ------------------------------------------------------------------
    # Simulating what a real cashier action would trigger: a status change,
    # normally followed by an `application.order.updated` webhook.
    # ------------------------------------------------------------------
    def _set_status(self, status, fire_webhook=True):
        for rec in self:
            rec.write({'status': status, 'state': STATUS_LABELS.get(status, str(status))})
            if fire_webhook and rec.app_id.webhook_url:
                rec._send_webhook()

    def _send_webhook(self):
        self.ensure_one()
        payload = {
            'timestamp': int(time.time()),
            'event': 'application.order.updated',
            'business': {
                'name': self.app_id.business_name,
                'reference': self.app_id.business_reference,
            },
            'order': {
                'id': self.foodics_id,
                'branch_id': self.branch_id,
                'type': self.order_type,
                'status': self.status,
                'total_price': self.total_price,
            },
        }
        try:
            requests.post(self.app_id.webhook_url, json=payload, timeout=10)
        except requests.RequestException as e:
            _logger.warning('Foodics mock: could not deliver webhook to %s: %s', self.app_id.webhook_url, e)

    def action_simulate_accepted(self):
        self._set_status(2)

    def action_simulate_closed(self):
        self._set_status(4)

    def action_simulate_declined(self):
        self._set_status(3)

    def action_simulate_missed_webhook(self):
        """Change status WITHOUT telling anyone (or telling Odoo through
        this button, but skipping the webhook call). Use this to confirm
        foodics_order_sync's reconciliation cron - or the manual "Refresh
        Status" button on foodics.order - catches up on its own even when a
        webhook never arrives.
        """
        self._set_status(4, fire_webhook=False)
