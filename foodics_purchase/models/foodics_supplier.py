from odoo import api, fields, models, _


class FoodicsSupplier(models.Model):
    """Odoo-side mirror of the Foodics 'Supplier' object (docs: Suppliers
    section - GET/POST /suppliers etc., scope inventory.settings.read).

    Each Foodics supplier is linked to a real res.partner so Odoo purchase
    orders can be raised against it. The link is created automatically on
    sync: match an existing supplier partner by email or code (ref), else
    create one.
    """
    _name = 'foodics.supplier'
    _description = 'Foodics Supplier'
    _rec_name = 'name'
    _order = 'name'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade',
                                index=True)
    foodics_id = fields.Char(required=True, string='Foodics ID')
    name = fields.Char(required=True)
    contact_name = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    code = fields.Char(help='Supplier code as configured in Foodics.')
    tags = fields.Char(help='Comma-separated Foodics tag ids.')
    item_ids = fields.One2many('foodics.supplier.item', 'supplier_id',
                               string='Supplied Items')
    partner_id = fields.Many2one(
        'res.partner', string='Vendor Partner',
        help='The Odoo vendor used on purchase orders for this Foodics supplier.')
    active = fields.Boolean(default=True)
    deleted_in_foodics = fields.Boolean(
        help='Upstream deleted_at was set; the record was archived locally.')
    synced_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        ('foodics_supplier_unique', 'unique(config_id, foodics_id)',
         'This Foodics supplier is already synced.'),
    ]

    # ------------------------------------------------------------------
    # sync  (implements docs "List Suppliers" + upsert semantics)
    # ------------------------------------------------------------------
    @api.model
    def action_sync_from_foodics(self, config=None):
        """Pull all suppliers (following pagination) and upsert them.

        Soft-deleted upstream suppliers archive their shadow record and the
        linked partner stays untouched for history. Implements the doc's
        is_deleted / updated_after filters implicitly by full refresh.
        """
        api = self.env['foodics.api']
        config = api._get_config(config)
        rows = api.fetch_suppliers(config)
        SupplierItem = self.env['foodics.supplier.item']
        Item = self.env['foodics.inventory.item']
        for row in rows:
            supplier = self.search([('config_id', '=', config.id),
                                    ('foodics_id', '=', row.get('id'))], limit=1)
            vals = self._vals_from_row(row)
            if supplier:
                supplier.write(vals)
            else:
                vals.update({'config_id': config.id,
                             'foodics_id': row.get('id')})
                supplier = self.create(vals)

            # pivot items -> make sure inventory items exist, then refresh pivots
            seen_item_ids = []
            for item_row in row.get('items') or []:
                item = Item.upsert_from_foodics(config, {'id': item_row.get('id')},
                                                shallow=True)
                if not item:
                    continue
                seen_item_ids.append(item.id)
                pivot = (item_row.get('pivot') or {})
                s_item = SupplierItem.search([('supplier_id', '=', supplier.id),
                                              ('item_id', '=', item.id)], limit=1)
                s_vals = {
                    'order_unit': pivot.get('order_unit'),
                    'order_to_storage_factor': float(pivot.get('order_to_storage_factor') or 1),
                    'minimum_order_quantity': float(pivot.get('minimum_order_quantity') or 0),
                    'cost': float(pivot.get('cost') or 0),
                    'code': pivot.get('code'),
                }
                if s_item:
                    s_item.write(s_vals)
                else:
                    s_vals.update({'supplier_id': supplier.id, 'item_id': item.id})
                    SupplierItem.create(s_vals)
            # drop pivots removed upstream
            supplier.item_ids.filtered(
                lambda l: l.item_id.id not in seen_item_ids).unlink()

            if not supplier.partner_id:
                supplier.partner_id = supplier._find_or_create_partner()

        # archive suppliers that vanished upstream (soft-delete per docs:
        # they keep deleted_at and can be restored via /suppliers/{id}/restore)
        fetched_ids = [r.get('id') for r in rows]
        stale = self.search([('config_id', '=', config.id),
                             ('foodics_id', 'not in', fetched_ids or [''])])
        stale.write({'deleted_in_foodics': True, 'active': False})
        return len(rows)

    def _vals_from_row(self, row):
        return {
            'name': row.get('name'),
            'contact_name': row.get('contact_name'),
            'phone': row.get('phone'),
            'email': row.get('email'),
            'code': row.get('code'),
            'tags': ', '.join(t.get('id', '') for t in row.get('tags') or []),
            'active': True,
            'deleted_in_foodics': bool(row.get('deleted_at')),
            'synced_at': fields.Datetime.now(),
        }

    def _find_or_create_partner(self):
        """Match an existing vendor partner (by ref==code, else email) or
        create one - so buyers can pick real Odoo vendors immediately."""
        self.ensure_one()
        Partner = self.env['res.partner']
        partner = Partner.browse()
        if self.code:
            partner = Partner.search([('ref', '=', self.code)], limit=1)
        if not partner and self.email:
            partner = Partner.search([('email', '=ilike', self.email)], limit=1)
        if not partner:
            partner = Partner.create({
                'name': self.name,
                'email': self.email or False,
                'phone': self.phone or False,
                'ref': self.code or False,
                'supplier_rank': 1,
                'comment': _('Created from Foodics supplier %s (%s)')
                           % (self.foodics_id, self.config_id.name),
            })
        return partner


class FoodicsSupplierItem(models.Model):
    """Odoo copy of the supplier<->inventory-item pivot documented under
    Suppliers: order_unit, order_to_storage_factor, minimum_order_quantity,
    cost, code. Drives PO payload building and MOQ validation."""
    _name = 'foodics.supplier.item'
    _description = 'Foodics Supplier / Inventory Item Pivot'
    _rec_name = 'display_label'

    supplier_id = fields.Many2one('foodics.supplier', required=True, ondelete='cascade',
                                  index=True)
    item_id = fields.Many2one('foodics.inventory.item', required=True,
                              ondelete='cascade', index=True)
    order_unit = fields.Char(string='Order Unit')
    order_to_storage_factor = fields.Float(default=1.0,
                                           string='Order → Storage Factor')
    minimum_order_quantity = fields.Float(string='Minimum Order Qty')
    cost = fields.Float(string='Unit Cost (order unit)')
    code = fields.Char()
    display_label = fields.Char(compute='_compute_display_label')

    def _compute_display_label(self):
        for rec in self:
            rec.display_label = f'{rec.supplier_id.name} / {rec.item_id.name}'

    _sql_constraints = [
        ('foodics_supplier_item_unique', 'unique(supplier_id, item_id)',
         'This item is already attached to the supplier.'),
    ]
