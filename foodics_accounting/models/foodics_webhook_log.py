from odoo import models


class FoodicsWebhookLog(models.Model):
    _inherit = 'foodics.webhook.log'

    # order.created / order.updated fire for every order; customer.order.*
    # fire for the subset that has a customer attached - same payload shape
    # either way (an "order" object), so one shared handler covers all four.
    # See foodics_order_sync's own `_handle_application_order_updated` for
    # the (unrelated) event it reacts to - that one is about orders *pushed*
    # from Odoo, this is about every Foodics POS sale.
    def _handle_order_created(self):
        self._handle_foodics_pos_order_event()

    def _handle_order_updated(self):
        self._handle_foodics_pos_order_event()

    def _handle_customer_order_created(self):
        self._handle_foodics_pos_order_event()

    def _handle_customer_order_updated(self):
        self._handle_foodics_pos_order_event()

    def _handle_foodics_pos_order_event(self):
        self.ensure_one()
        payload = self.get_payload()
        order_data = payload.get('order') or {}
        if not order_data.get('id'):
            raise ValueError('Webhook payload has no order.id')
        self.env['foodics.pos.order']._sync_from_payload(self.config_id, order_data)
