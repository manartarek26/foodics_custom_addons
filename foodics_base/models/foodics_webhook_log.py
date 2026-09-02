import json
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class FoodicsWebhookLog(models.Model):
    """Every inbound Foodics webhook call lands here first, no matter its
    event type. A module that cares about a specific event reacts to it by
    inheriting this model and adding a method named
    `_handle_<event, dots and dashes replaced by underscores>` - e.g.
    `_handle_application_order_updated` for the `application.order.updated`
    event. That's it: no registry to update, nothing to change in this base
    module when a new integration needs a new event.
    """
    _name = 'foodics.webhook.log'
    _description = 'Foodics Webhook Log'
    _order = 'create_date desc'
    _rec_name = 'event'

    config_id = fields.Many2one('foodics.config', ondelete='cascade')
    event = fields.Char(required=True)
    raw_payload = fields.Text(required=True)
    state = fields.Selection([
        ('new', 'New'),
        ('processed', 'Processed'),
        ('ignored', 'Ignored (no handler installed)'),
        ('error', 'Error'),
    ], default='new')
    error_message = fields.Text(readonly=True)

    def get_payload(self):
        self.ensure_one()
        return json.loads(self.raw_payload or '{}')

    def process(self):
        """Dispatch to `_handle_<event>()` if some installed module
        implements it for this event.
        """
        for rec in self:
            handler_name = '_handle_' + rec.event.replace('.', '_').replace('-', '_')
            handler = getattr(rec, handler_name, None)
            if not handler:
                rec.state = 'ignored'
                continue
            try:
                handler()
                rec.write({'state': 'processed', 'error_message': False})
            except Exception as e:  # noqa: BLE001 - a webhook must never blow up the request/queue;
                # the failure is recorded here for a human to look at and
                # reprocess, instead of crashing or silently vanishing.
                _logger.exception('Foodics webhook handler %s failed', handler_name)
                rec.write({'state': 'error', 'error_message': str(e)})

    def action_reprocess(self):
        self.process()
