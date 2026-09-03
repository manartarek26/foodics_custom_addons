from odoo import api, fields, models, _

# Common Foodics storage/order unit strings (docs use free strings such as
# "box", "Kg", "gram" - the API does not standardize them) mapped onto Odoo
# UoM names for automatic guessing. Anything unmatched must be mapped
# manually on the item before it can be used in purchase orders.
UOM_ALIASES = {
    # xmlids live in the uom module (verified: product_uom_kgm/_gram/_lb/
    # _oz/_litre/_unit); anything unmatched must be mapped manually.
    'uom.product_uom_kgm': ['kg', 'kgs', 'kilogram', 'kilograms', 'kilo', 'kilos'],
    'uom.product_uom_gram': ['g', 'gr', 'gram', 'grams', 'gramme', 'grammes'],
    'uom.product_uom_lb': ['lb', 'lbs', 'pound', 'pounds'],
    'uom.product_uom_oz': ['oz', 'ounce', 'ounces'],
    'uom.product_uom_litre': ['l', 'ltr', 'liter', 'litre', 'liters', 'litres'],
    'uom.product_uom_unit': ['unit', 'units', 'pcs', 'pc', 'piece', 'pieces',
                             'ea', 'each', 'box', 'carton', 'case'],
}


class FoodicsInventoryItem(models.Model):
    """Odoo-side mirror of the Foodics 'Inventory Item' object (docs:
    Inventory Items section - raw/produced items tracked through inventory
    transactions). Linked to a real product.product so receipts and costs
    land on actual Odoo stock.

    NOTE: Foodics "menu Products" (the sale-side product mapping in
    foodics_base / foodics_order_sync) and these inventory items are
    DIFFERENT entities upstream; purchases always use inventory items.
    """
    _name = 'foodics.inventory.item'
    _description = 'Foodics Inventory Item'
    _rec_name = 'name'
    _order = 'name'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade',
                                index=True)
    foodics_id = fields.Char(required=True, string='Foodics ID')
    name = fields.Char(required=True)
    sku = fields.Char()
    barcode = fields.Char()
    storage_unit = fields.Char(help="Unit in which Foodics tracks this item's stock "
                              "(free string per docs, e.g. box / Kg / gram).")
    ingredient_unit = fields.Char()
    storage_to_ingredient_factor = fields.Float(default=1.0)
    costing_method = fields.Selection([
        ('1', 'Fixed Cost'),
        ('2', 'Calculate cost from ingredients'),
    ], default='1')
    cost = fields.Float(string='Cost (storage unit)')
    minimum_level = fields.Float()
    maximum_level = fields.Float()
    par_level = fields.Float()
    is_product = fields.Boolean(help='Item is also sold as a finished product upstream.')
    category_name = fields.Char()
    supplier_line_ids = fields.One2many('foodics.supplier.item', 'item_id',
                                        string='Supplied By')
    product_id = fields.Many2one(
        'product.product', string='Linked Odoo Product',
        help='Receipts/returns of this item are posted on this product.')
    odoo_uom_id = fields.Many2one(
        'uom.uom', string='Odoo UoM (Storage Unit)',
        help='Odoo unit equivalent to the Foodics storage_unit. Guessed '
             'automatically when possible; otherwise set it manually.')
    active = fields.Boolean(default=True)
    deleted_in_foodics = fields.Boolean()
    synced_at = fields.Datetime(readonly=True)

    _sql_constraints = [
        ('foodics_item_unique', 'unique(config_id, foodics_id)',
         'This Foodics inventory item is already synced.'),
    ]

    uom_mapped = fields.Boolean(compute='_compute_uom_mapped')

    def _compute_uom_mapped(self):
        for rec in self:
            rec.uom_mapped = bool(rec.odoo_uom_id)

    # ------------------------------------------------------------------
    # sync  (implements docs "List Inventory Items")
    # ------------------------------------------------------------------
    @api.model
    def action_sync_from_foodics(self, config=None):
        api = self.env['foodics.api']
        config = api._get_config(config)
        rows = api.fetch_inventory_items(config)
        created_products = 0
        for row in rows:
            item = self.upsert_from_foodics(config, row)
            if item and not item.product_id and config.po_autocreate_items:
                if item._create_odoo_product():
                    created_products += 1
        fetched_ids = [r.get('id') for r in rows]
        stale = self.search([('config_id', '=', config.id),
                             ('foodics_id', 'not in', fetched_ids or [''])])
        stale.write({'deleted_in_foodics': True, 'active': False})
        return len(rows), created_products

    @api.model
    def upsert_from_foodics(self, config, row, shallow=False):
        """Create/update one shadow record from a doc-shaped dict.

        shallow=True is used while syncing suppliers (only the id is known
        there); missing names are filled later by the full items sync.
        """
        vals = {
            'deleted_in_foodics': bool(row.get('deleted_at')),
            'active': not row.get('deleted_at'),
            'synced_at': fields.Datetime.now(),
        }
        if not shallow:
            category = row.get('category') or {}
            vals.update({
                'name': row.get('name') or row.get('sku') or _('Unnamed item'),
                'sku': row.get('sku'),
                'barcode': row.get('barcode'),
                'storage_unit': row.get('storage_unit'),
                'ingredient_unit': row.get('ingredient_unit'),
                'storage_to_ingredient_factor':
                    float(row.get('storage_to_ingredient_factor') or 1),
                # docs Costing Methods: 1 Fixed, 2 from ingredients
                'costing_method': str(row.get('costing_method') or 1),
                'cost': float(row.get('cost') or 0),
                'minimum_level': float(row.get('minimum_level') or 0),
                'maximum_level': float(row.get('maximum_level') or 0),
                'par_level': float(row.get('par_level') or 0),
                'is_product': bool(row.get('is_product')),
                'category_name': category.get('name'),
            })
        else:
            existing = self.search([('config_id', '=', config.id),
                                    ('foodics_id', '=', row.get('id'))], limit=1)
            if existing:
                return existing
            # Stub: the suppliers endpoint only carries item ids (docs
            # Suppliers sample). Create a placeholder that the full
            # inventory-items sync will fill in afterwards.
            stub = self.create({
                'config_id': config.id,
                'foodics_id': row.get('id'),
                'name': row.get('name') or row.get('id') or _('Unknown item'),
                'synced_at': fields.Datetime.now(),
            })
            return stub
        item = self.search([('config_id', '=', config.id),
                            ('foodics_id', '=', row.get('id'))], limit=1)
        if item:
            item.write(vals)
            # keep the name even if only shallow data arrived earlier
            if not item.name:
                item.name = vals.get('name')
        else:
            vals.update({'config_id': config.id, 'foodics_id': row.get('id')})
            item = self.create(vals)
        if not item.odoo_uom_id:
            item.odoo_uom_id = item._match_uom(item.storage_unit)
        if not item.product_id and item.sku and not shallow:
            item.product_id = self._find_product(item)
        return item

    def _find_product(self, item):
        Product = self.env['product.product']
        if item.barcode:
            product = Product.search([('barcode', '=', item.barcode)], limit=1)
            if product:
                return product
        if item.sku:
            product = Product.search([('default_code', '=', item.sku)], limit=1)
            if product:
                return product
        return Product.browse()

    def _create_odoo_product(self):
        """Optional helper: create the linked storable product."""
        self.ensure_one()
        if self.product_id or not self.name:
            return False
        product = self.env['product.product'].create({
            'name': self.name,
            'default_code': self.sku or False,
            'barcode': self.barcode or False,
            'is_storable': True,
            'uom_id': (self.odoo_uom_id or self.env.ref('uom.product_uom_unit')).id,
            'uom_po_id': (self.odoo_uom_id or self.env.ref('uom.product_uom_unit')).id,
            'standard_price': self.cost,
            'purchase_ok': True,
            'sale_ok': False,
        })
        self.product_id = product
        return True

    # ------------------------------------------------------------------
    # UoM matching (best-effort guess; manual mapping always possible)
    # ------------------------------------------------------------------
    @api.model
    def _match_uom(self, storage_unit):
        """Try to map a Foodics unit string ('Kg', 'box', ...) onto an Odoo
        uom.uom. Returns an empty recordset when unsure - PO confirmation
        will then require a manual mapping instead of guessing silently.

        NOTE: mapping 'box/case/carton' to generic Units is a deliberate,
        documented simplification (docs don't standardize unit strings);
        buyers can override the mapping per item.
        """
        if not storage_unit:
            return self.env['uom.uom'].browse()
        needle = storage_unit.strip().lower()
        for xmlid, aliases in UOM_ALIASES.items():
            if needle in aliases:
                try:
                    return self.env['ir.model.data'].sudo()._load_xmlid(xmlid)
                except ValueError:
                    continue
        # last resort: case-insensitive name match on any active UoM
        return self.env['uom.uom'].search([('name', '=ilike', needle)], limit=1)

    # ------------------------------------------------------------------
    # conversion helpers (doc pivots: order_to_storage_factor /
    # unit_to_storage_factor - "conversion factor between order and
    # storage units")
    # ------------------------------------------------------------------
    def order_qty_to_storage(self, qty_order_units, factor):
        factor = factor or 1.0
        return qty_order_units * factor

    def storage_cost_per_unit(self, price_per_order_unit, factor):
        """Foodics transaction item pivot carries quantity+cost only (no
        unit field), so quantities/costs are sent expressed in STORAGE
        units. This keeps line totals identical:
        qty_order x price_order == (qty_order x factor) x (price_order/factor).
        """
        factor = factor or 1.0
        return price_per_order_unit / factor if factor else price_per_order_unit
