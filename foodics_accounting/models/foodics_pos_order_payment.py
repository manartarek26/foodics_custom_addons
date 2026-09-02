from odoo import fields, models


class FoodicsPosOrderPayment(models.Model):
    _name = 'foodics.pos.order.payment'
    _description = 'Foodics POS Order Payment'
    _order = 'id'

    order_id = fields.Many2one('foodics.pos.order', required=True, ondelete='cascade')
    foodics_payment_id = fields.Char(string='Foodics Payment ID')
    foodics_payment_method_id = fields.Char(
        string='Foodics Payment Method ID',
        help='Raw Foodics payment-method id, kept even if it could not be resolved to a '
             'foodics.payment.method row (or that row had no journal yet) at the time this '
             'payment was recorded - so it can be re-resolved later once mapped, on Retry.')
    payment_method_id = fields.Many2one('foodics.payment.method', string='Payment Method')
    amount = fields.Float(required=True)
    tendered = fields.Float()
    tips = fields.Float()
    business_date = fields.Date()
    account_payment_id = fields.Many2one(
        'account.payment', readonly=True, copy=False,
        help='Set once this Foodics payment has been registered and reconciled against the '
             'invoice/credit note as an Odoo payment.')

    _sql_constraints = [
        ('foodics_pos_order_payment_unique', 'unique(order_id, foodics_payment_id)',
         'This Foodics payment has already been recorded for this order.'),
    ]
