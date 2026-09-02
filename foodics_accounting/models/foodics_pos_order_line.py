from odoo import fields, models


class FoodicsPosOrderLine(models.Model):
    """One row per product/combo-item/charge on a Foodics order. `price_subtotal` is
    always the Foodics-computed *tax-exclusive total for this line as a whole*
    (their `tax_exclusive_total_price` / `tax_exclusive_amount`), not a unit price -
    for a product with modifier options, Foodics folds the options' own totals into
    this same figure, so one invoice line per top-level product/charge (using its
    own tax list) already reproduces the correct tax, without modelling options as
    separate lines.
    """
    _name = 'foodics.pos.order.line'
    _description = 'Foodics POS Order Line'
    _order = 'sequence, id'

    order_id = fields.Many2one('foodics.pos.order', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    foodics_line_id = fields.Char(string='Foodics Line ID')
    name = fields.Char(required=True)
    is_charge = fields.Boolean(help='True for a Foodics "charge" (service charge, delivery fee, ...) '
                                     'rather than a menu product.')
    is_combo = fields.Boolean(help='True if this line came from inside a combo rather than a top-level product.')
    product_mapping_id = fields.Many2one(
        'foodics.product.mapping', string='Foodics Product',
        help='Resolved from foodics.product.mapping when the underlying Foodics product ID is '
             'known and mapped/approved. Left empty (line posts without a product) otherwise, or '
             'for charges - which have no Foodics product at all.')
    quantity = fields.Float(default=1.0)
    discount_amount = fields.Float(help='Foodics-reported discount already netted into price_subtotal below; '
                                          'kept here for traceability only.')
    price_subtotal = fields.Float(string='Tax-Excl. Total', help='Foodics tax_exclusive_total_price/'
                                                                    'tax_exclusive_amount for this line.')
    tax_ids = fields.Many2many('account.tax', string='Taxes',
                                help='Resolved from foodics.tax.mapping for each Foodics tax on this line.')
