from odoo import api, fields, models, _


class FoodicsConfig(models.Model):
    _inherit = 'foodics.config'

    default_customer_id = fields.Many2one(
        'res.partner', string='Default Customer',
        help='Used for Foodics orders with no customer attached (walk-ins) - most POS sales. '
             'Orders with a Foodics customer instead get/create a matching Odoo contact.')
    fallback_product_id = fields.Many2one(
        'product.product', string='Fallback Product (POS order lines)',
        help='A POS order cannot have a line with no product (Odoo blocks it outright) - so '
             'this is used for any order line with no Foodics product mapping (Foodics > '
             'Product Mapping) and for Foodics charges/service fees, which never have a product '
             'at all. A simple generic service product (e.g. "Foodics Charge") works fine - its '
             'own price/taxes are ignored, only the line\'s own amount is used. Required before '
             'any order with an unmapped product or a charge can be invoiced.')
    last_order_reference = fields.Integer(
        default=0, copy=False,
        help='Foodics order `number` of the last order pulled - the next pull only asks Foodics '
             'for orders after this one (filter[reference_after]), per their pagination guide.')

    tax_mapping_count = fields.Integer(compute='_compute_accounting_counts')
    payment_method_count = fields.Integer(compute='_compute_accounting_counts')
    pos_order_count = fields.Integer(compute='_compute_accounting_counts')

    def _compute_accounting_counts(self):
        for rec in self:
            rec.tax_mapping_count = self.env['foodics.tax.mapping'].search_count([('config_id', '=', rec.id)])
            rec.payment_method_count = self.env['foodics.payment.method'].search_count(
                [('config_id', '=', rec.id)])
            rec.pos_order_count = self.env['foodics.pos.order'].search_count([('config_id', '=', rec.id)])

    # ------------------------------------------------------------------
    # Master data sync
    # ------------------------------------------------------------------
    def action_sync_taxes(self):
        self.ensure_one()
        data = self._get_all('/taxes')
        created, updated = self.env['foodics.tax.mapping']._sync_from_foodics(self, data)
        return self._notify(
            _('Taxes synced'), _('%s created, %s updated. Map each to an Odoo tax under Foodics > Tax Mapping.')
            % (created, updated))

    def action_sync_payment_methods(self):
        self.ensure_one()
        data = self._get_all('/payment_methods')
        created, updated = self.env['foodics.payment.method']._sync_from_foodics(self, data)
        return self._notify(
            _('Payment methods synced'),
            _('%s created, %s updated. Map each to an Odoo POS payment method under Foodics > Payment Methods.')
            % (created, updated))

    # ------------------------------------------------------------------
    # Orders pull (the safety net under the webhook - see foodics.pos.order)
    # ------------------------------------------------------------------
    def action_pull_orders(self):
        self.ensure_one()
        params = {
            'filter[status]': '4,5',
            'sort': 'reference',
            'filter[reference_after]': self.last_order_reference or 0,
            'include': 'branch,customer,charges,payments.payment_method,discount,'
                       'products.discount,products.taxes,products.options.taxes,'
                       'combos.discount,combos.products.taxes,combos.products.options.taxes',
        }
        data = self._get_all('/orders', params=params)
        Order = self.env['foodics.pos.order']
        max_reference = self.last_order_reference or 0
        for rec in data:
            Order._sync_from_payload(self, rec)
            max_reference = max(max_reference, rec.get('number') or 0)
        self.last_order_reference = max_reference
        return self._notify(_('Orders pulled'), _('%s order(s) processed.') % len(data))

    @api.model
    def _cron_pull_orders(self):
        for config in self.search([('access_token', '!=', False)]):
            config.action_pull_orders()

    # ------------------------------------------------------------------
    # Smart button openers
    # ------------------------------------------------------------------
    def action_open_tax_mappings(self):
        return self._open_related('foodics.tax.mapping', _('Foodics Tax Mapping'))

    def action_open_payment_methods(self):
        return self._open_related('foodics.payment.method', _('Foodics Payment Methods'))

    def action_open_pos_orders(self):
        return self._open_related('foodics.pos.order', _('Foodics POS Orders'))
