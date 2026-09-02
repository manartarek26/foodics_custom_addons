import json
import logging
import time
import uuid

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Mirrors the canned products/taxes/payment methods already served by
# GET /products, /taxes, /payment_methods (see sample_data.py) - a line here
# has to reference one of these ids for the resulting order payload to match
# what a real pull sync would also see for the same ids.
PRODUCTS = {
    'mock-prod-burger': {'name': 'Burger', 'price': 28.0, 'taxable': True},
    'mock-prod-pepsi': {'name': 'Pepsi', 'price': 6.0, 'taxable': True},
    'mock-prod-milk': {'name': 'Milk', 'price': 4.5, 'taxable': False},
}
BRANCHES = {
    'mock-branch-001': 'Main Branch (Mock)',
    'mock-branch-002': 'Drive Thru Branch (Mock)',
}
PAYMENT_METHODS = {
    'mock-pm-cash': {'name': 'Cash (Mock)', 'type': 1},
    'mock-pm-card': {'name': 'Card (Mock)', 'type': 2},
}
TAX_VAT15 = {'id': 'mock-tax-vat15', 'name': 'VAT 15% (Mock)', 'rate': 15}

STATUS_LABELS = {1: 'Pending', 4: 'Closed', 5: 'Returned'}


class FoodicsMockPosOrderLine(models.Model):
    _name = 'foodics.mock.pos.order.line'
    _description = 'Foodics Mock POS Order Line'

    order_id = fields.Many2one('foodics.mock.pos.order', required=True, ondelete='cascade')
    product = fields.Selection([(k, v['name']) for k, v in PRODUCTS.items()], required=True,
                                default='mock-prod-burger')
    quantity = fields.Float(default=1.0)
    unit_price = fields.Float()
    discount_amount = fields.Float()
    taxable = fields.Boolean(default=True)

    @api.onchange('product')
    def _onchange_product(self):
        for rec in self:
            info = PRODUCTS.get(rec.product)
            if info:
                rec.unit_price = info['price']
                rec.taxable = info['taxable']

    @api.model_create_multi
    def create(self, vals_list):
        # The onchange above only fires from the form UI - default unit_price/taxable from the
        # product here too, so a line created programmatically (script, XML-RPC, a future
        # scripted test) without them still gets sane values instead of silently pricing at 0.
        for vals in vals_list:
            info = PRODUCTS.get(vals.get('product'))
            if info:
                vals.setdefault('unit_price', info['price'])
                if 'taxable' not in vals:
                    vals['taxable'] = info['taxable']
        return super().create(vals_list)

    def _tax_exclusive_total(self):
        self.ensure_one()
        return self.quantity * self.unit_price - self.discount_amount

    def _to_payload(self):
        self.ensure_one()
        tax_excl = self._tax_exclusive_total()
        tax_amount = round(tax_excl * TAX_VAT15['rate'] / 100.0, 2) if self.taxable else 0.0
        taxes = [{'id': TAX_VAT15['id'], 'name': TAX_VAT15['name'], 'rate': TAX_VAT15['rate'],
                  'pivot': {'amount': tax_amount, 'rate': TAX_VAT15['rate']}}] if self.taxable else []
        return {
            'id': f'{self.order_id.foodics_id}-line-{self.id}',
            'product': {'id': self.product, 'name': PRODUCTS[self.product]['name']},
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'discount_amount': self.discount_amount,
            'tax_exclusive_unit_price': self.unit_price,
            'tax_exclusive_total_price': tax_excl,
            'total_price': tax_excl + tax_amount,
            'status': 3,
            'taxes': taxes,
            'options': [],
        }, tax_excl, tax_amount


class FoodicsMockPosOrderPayment(models.Model):
    _name = 'foodics.mock.pos.order.payment'
    _description = 'Foodics Mock POS Order Payment'

    order_id = fields.Many2one('foodics.mock.pos.order', required=True, ondelete='cascade')
    method = fields.Selection([(k, v['name']) for k, v in PAYMENT_METHODS.items()], required=True,
                               default='mock-pm-cash')
    amount = fields.Float(required=True)
    tips = fields.Float()

    def _to_payload(self):
        self.ensure_one()
        return {
            'id': f'{self.order_id.foodics_id}-pay-{self.id}',
            'amount': self.amount,
            'tendered': self.amount,
            'tips': self.tips,
            'business_date': fields.Date.to_string(self.order_id.business_date),
            'payment_method': {'id': self.method, 'name': PAYMENT_METHODS[self.method]['name'],
                                'type': PAYMENT_METHODS[self.method]['type']},
        }


class FoodicsMockPosOrder(models.Model):
    """A POS sale you build by hand (products, taxes, charge, payments, optional customer) and
    then fire at foodics_accounting exactly the way the real Foodics till would: as an
    `order.created`/`order.updated` webhook carrying the full order object, in the same shape
    served by GET /orders. Distinct from `foodics.mock.order`, which stands in for orders
    *pushed* to Foodics by foodics_order_sync - this one is a POS sale that originates here,
    the way most real Foodics orders do.
    """
    _name = 'foodics.mock.pos.order'
    _description = 'Foodics Mock POS Order (accounting simulation)'
    _rec_name = 'foodics_id'
    _order = 'number desc'

    app_id = fields.Many2one('foodics.mock.app', required=True, ondelete='cascade')
    foodics_id = fields.Char(required=True, copy=False, default=lambda self: str(uuid.uuid4()))
    number = fields.Integer(copy=False, help='Sequential per app, mirrors Foodics order.number.')
    branch = fields.Selection([(k, v) for k, v in BRANCHES.items()], required=True, default='mock-branch-001')
    business_date = fields.Date(default=fields.Date.context_today)

    status = fields.Integer(default=1, copy=False)
    status_label = fields.Char(compute='_compute_status_label')

    customer_name = fields.Char(help='Leave empty to simulate a walk-in (no Foodics customer on the order).')
    customer_phone = fields.Char()
    customer_email = fields.Char()
    customer_ref = fields.Char(
        compute='_compute_customer_ref', store=True,
        help='Stable id sent as customer.id - derived from name+phone so the same person '
             'resolves to the same Odoo partner across multiple orders, like a real Foodics '
             'customer id would.')

    line_ids = fields.One2many('foodics.mock.pos.order.line', 'order_id', copy=True)
    payment_ids = fields.One2many('foodics.mock.pos.order.payment', 'order_id', copy=True)

    charge_name = fields.Char(default='Service Charge')
    charge_amount = fields.Float(help='Tax-exclusive amount of an optional order-level charge. 0 = no charge line.')
    charge_taxable = fields.Boolean(default=True)

    rounding_amount = fields.Float()

    return_of_id = fields.Many2one(
        'foodics.mock.pos.order', copy=False, readonly=True,
        help='Set on the order created by "Return This Order" - the original sale being returned.')

    subtotal_price = fields.Float(compute='_compute_totals')
    tax_amount = fields.Float(compute='_compute_totals')
    total_price = fields.Float(compute='_compute_totals')
    payments_total = fields.Float(compute='_compute_totals')
    payments_balanced = fields.Boolean(compute='_compute_totals',
                                        help='True once payment lines add up to the order total - '
                                             'a real till would not let you close otherwise.')

    last_event_sent = fields.Char(readonly=True, copy=False)
    last_payload_sent = fields.Text(readonly=True, copy=False)

    _sql_constraints = [
        ('foodics_mock_pos_order_unique', 'unique(foodics_id)', 'This mock order id is already used.'),
    ]

    @api.depends('customer_name', 'customer_phone')
    def _compute_customer_ref(self):
        for rec in self:
            rec.customer_ref = (
                f'mock-cust-{abs(hash((rec.customer_name or "", rec.customer_phone or "")))}'
                if rec.customer_name else False)

    @api.depends('status')
    def _compute_status_label(self):
        for rec in self:
            rec.status_label = STATUS_LABELS.get(rec.status, str(rec.status))

    @api.depends('line_ids.quantity', 'line_ids.unit_price', 'line_ids.discount_amount', 'line_ids.taxable',
                 'charge_amount', 'charge_taxable', 'rounding_amount', 'payment_ids.amount')
    def _compute_totals(self):
        for rec in self:
            subtotal = tax = 0.0
            for line in rec.line_ids:
                _payload, tax_excl, tax_amt = line._to_payload()
                subtotal += tax_excl
                tax += tax_amt
            if rec.charge_amount:
                subtotal += rec.charge_amount
                if rec.charge_taxable:
                    tax += round(rec.charge_amount * TAX_VAT15['rate'] / 100.0, 2)
            rec.subtotal_price = subtotal
            rec.tax_amount = tax
            rec.total_price = subtotal + tax + rec.rounding_amount
            rec.payments_total = sum(rec.payment_ids.mapped('amount'))
            rec.payments_balanced = abs(rec.payments_total - rec.total_price) < 0.01

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('number') and vals.get('app_id'):
                last = self.search([('app_id', '=', vals['app_id'])], order='number desc', limit=1)
                vals['number'] = (last.number or 0) + 1
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Payload building - same shape GET /orders/<id> serves, and the same
    # shape a real Foodics order.* webhook carries under its "order" key.
    # ------------------------------------------------------------------
    def _to_payload(self):
        self.ensure_one()
        products = []
        for line in self.line_ids:
            payload, _te, _ta = line._to_payload()
            products.append(payload)

        charges = []
        if self.charge_amount:
            tax_amt = round(self.charge_amount * TAX_VAT15['rate'] / 100.0, 2) if self.charge_taxable else 0.0
            charges.append({
                'charge': {'id': 'mock-charge-1', 'name': self.charge_name},
                'id': f'{self.foodics_id}-charge',
                'amount': self.charge_amount + tax_amt,
                'tax_exclusive_amount': self.charge_amount,
                'taxes': [{'id': TAX_VAT15['id'], 'name': TAX_VAT15['name'], 'rate': TAX_VAT15['rate'],
                           'pivot': {'amount': tax_amt, 'rate': TAX_VAT15['rate']}}] if self.charge_taxable else [],
            })

        customer = None
        if self.customer_name:
            customer = {'id': self.customer_ref, 'name': self.customer_name,
                        'phone': self.customer_phone, 'email': self.customer_email}

        return {
            'id': self.foodics_id,
            'number': self.number,
            'type': 1,
            'status': self.status,
            'branch': {'id': self.branch, 'name': BRANCHES[self.branch], 'reference': self.branch},
            'customer': customer,
            'original_order': {'id': self.return_of_id.foodics_id} if self.return_of_id else None,
            'business_date': fields.Date.to_string(self.business_date),
            'opened_at': fields.Datetime.to_string(fields.Datetime.now()),
            'closed_at': fields.Datetime.to_string(fields.Datetime.now()) if self.status in (4, 5) else None,
            'subtotal_price': self.subtotal_price,
            'discount_amount': 0,
            'rounding_amount': self.rounding_amount,
            'total_price': self.total_price,
            'products': products,
            'combos': [],
            'charges': charges,
            'payments': [p._to_payload() for p in self.payment_ids],
        }

    def _send_webhook(self, event):
        self.ensure_one()
        if not self.app_id.webhook_url:
            raise UserError(_('Set a Webhook URL on this mock order\'s Fake App first (Foodics Mock > Fake Apps) - '
                               'that\'s where the Foodics Connection\'s webhook URL goes, same as for regular '
                               'order-sync testing.'))
        payload = {
            'timestamp': int(time.time()),
            'event': event,
            'business': {'name': self.app_id.business_name, 'reference': self.app_id.business_reference},
            'order': self._to_payload(),
        }
        try:
            resp = requests.post(self.app_id.webhook_url, json=payload, timeout=10)
            result = f'{resp.status_code} {resp.text[:200]}'
        except requests.RequestException as e:
            result = f'delivery failed: {e}'
            _logger.warning('Foodics mock: could not deliver webhook to %s: %s', self.app_id.webhook_url, e)
        self.write({'last_event_sent': f'{event} -> {result}',
                    'last_payload_sent': json.dumps(payload, indent=2, default=str)})

    # ------------------------------------------------------------------
    # Buttons - mirrors what happens at a real till: an order opens, gets
    # built up, then closes (or a return gets rung up as its own order).
    # ------------------------------------------------------------------
    def action_send_created(self):
        for rec in self:
            rec._send_webhook('order.created' if not rec.customer_ref else 'customer.order.created')

    def action_close_and_notify(self):
        for rec in self:
            if rec.status in (4, 5):
                raise UserError(_('Already closed/returned - use "Resend Update" to notify again.'))
            if not rec.payments_balanced:
                raise UserError(_('Payments (%.2f) do not add up to the order total (%.2f) - a real till '
                                   'would not let you close this order yet.') % (rec.payments_total, rec.total_price))
            rec.status = 4
            rec._send_webhook('order.updated' if not rec.customer_ref else 'customer.order.updated')

    def action_create_return(self):
        """Rings up a brand-new order representing a return of this one, status 5 - matching
        how Foodics itself models returns (a separate order object), not a status flip on the
        original. Copies this order's lines/payments 1:1 (a full return); edit the copy's
        quantities/payments by hand first if you want to simulate a partial return.
        """
        self.ensure_one()
        if self.status != 4:
            raise UserError(_('Can only return a Closed order.'))
        ret = self.copy({
            'foodics_id': str(uuid.uuid4()),
            'number': False,
            'status': 5,
            'return_of_id': self.id,
            'last_event_sent': False,
            'last_payload_sent': False,
        })
        ret._send_webhook('order.updated' if not ret.customer_ref else 'customer.order.updated')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Return Order'),
            'res_model': 'foodics.mock.pos.order',
            'view_mode': 'form',
            'res_id': ret.id,
        }

    def action_resend_update(self):
        for rec in self:
            if rec.status not in (4, 5):
                raise UserError(_('Close (or return) this order first.'))
            rec._send_webhook('order.updated' if not rec.customer_ref else 'customer.order.updated')

    def action_close_without_notifying(self):
        """Changes status to Closed WITHOUT firing a webhook - use this to test foodics_
        accounting's pull cron / "Pull Orders" button picking up what the webhook missed."""
        for rec in self:
            if rec.status in (4, 5):
                raise UserError(_('Already closed/returned.'))
            if not rec.payments_balanced:
                raise UserError(_('Payments do not add up to the order total yet.'))
            rec.status = 4
            rec.write({'last_event_sent': '(status changed, webhook NOT sent - by design)'})
