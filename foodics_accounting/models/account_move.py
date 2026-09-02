from odoo import fields, models, _


class AccountMove(models.Model):
    _inherit = 'account.move'

    foodics_pos_order_ids = fields.One2many(
        'foodics.pos.order', 'invoice_id', string='Foodics Order',
        help='The Foodics POS order this invoice/credit note was generated from.')
    foodics_pos_order_count = fields.Integer(compute='_compute_foodics_pos_order_count')

    def _compute_foodics_pos_order_count(self):
        for rec in self:
            rec.foodics_pos_order_count = len(rec.foodics_pos_order_ids)

    def action_open_foodics_pos_orders(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Foodics Order'),
            'res_model': 'foodics.pos.order',
            'view_mode': 'list,form',
            'domain': [('invoice_id', '=', self.id)],
        }
