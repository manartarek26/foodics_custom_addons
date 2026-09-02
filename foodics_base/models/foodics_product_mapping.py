from odoo import api, fields, models, _


class FoodicsProductMapping(models.Model):
    """One row per Foodics menu product, for one Foodics connection.

    This is deliberately split from `product.product` itself: the mapping
    row always exists once a product has been *seen* during a menu sync,
    independently of whether it has been approved yet, so nothing about a
    pending approval queue depends on an Odoo product existing.
    """
    _name = 'foodics.product.mapping'
    _description = 'Foodics Product Mapping'
    _rec_name = 'name'

    config_id = fields.Many2one('foodics.config', required=True, ondelete='cascade')
    foodics_id = fields.Char(required=True, string='Foodics ID')
    name = fields.Char(required=True)
    sku = fields.Char(string='SKU')
    price = fields.Float()
    category_name = fields.Char()
    is_active = fields.Boolean(
        default=True,
        help='As reported by Foodics. Informational only - we never auto-deactivate an Odoo '
             'product just because Foodics flips this, in case that product was matched to '
             'something already used elsewhere in Odoo (see "Matched Existing Product" below).')

    state = fields.Selection([
        ('to_approve', 'To Approve'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='to_approve', required=True)

    product_id = fields.Many2one(
        'product.product', string='Odoo Product', readonly=True, copy=False,
        help='Set once this mapping is approved: either the product created for it, or an '
             'already-existing Odoo product it was matched to.')
    matched_existing_product = fields.Boolean(
        readonly=True, copy=False,
        help='True if approving this mapping linked an Odoo product that already existed '
             '(matched by internal reference / barcode) instead of creating a new one.')
    note = fields.Char(readonly=True, copy=False)

    _sql_constraints = [
        ('foodics_product_mapping_unique', 'unique(config_id, foodics_id)',
         'This Foodics product is already synced (see the existing Product Mapping row instead '
         'of creating a duplicate).'),
    ]

    # ------------------------------------------------------------------
    # Sync entry point, called from foodics.config.action_sync_menu()
    # ------------------------------------------------------------------
    @api.model
    def _sync_from_foodics(self, config, records):
        require_approval = config.company_id.foodics_product_requires_approval
        created = updated = 0
        for rec in records:
            foodics_id = rec.get('id')
            if not foodics_id:
                continue
            mapping = self.search([('config_id', '=', config.id), ('foodics_id', '=', foodics_id)], limit=1)
            vals = {
                'config_id': config.id,
                'foodics_id': foodics_id,
                'name': rec.get('name'),
                'sku': rec.get('sku'),
                'price': rec.get('price') or 0.0,
                'is_active': rec.get('is_active', True),
                'category_name': (rec.get('category') or {}).get('name'),
            }
            if mapping:
                # Only refresh descriptive fields - never touch state/product_id
                # here, an already-approved/rejected decision must not be
                # silently reopened by a later sync.
                mapping.write(vals)
                updated += 1
            else:
                vals['state'] = 'approved' if not require_approval else 'to_approve'
                mapping = self.create(vals)
                created += 1
                if not require_approval:
                    mapping._create_or_link_product()
        return created, updated

    # ------------------------------------------------------------------
    # Approval / duplicate handling
    # ------------------------------------------------------------------
    def _find_matching_odoo_product(self):
        """Best-effort duplicate check: does an Odoo product that isn't
        already linked to *this* mapping look like the same item Foodics is
        describing? Matched by internal reference first (that's where a SKU
        usually ends up), then barcode, since restaurants often reuse the
        barcode field for a POS code.
        """
        self.ensure_one()
        Product = self.env['product.product']
        if not self.sku:
            return Product
        match = Product.search([('default_code', '=', self.sku)], limit=1)
        if not match:
            match = Product.search([('barcode', '=', self.sku)], limit=1)
        return match

    def _create_or_link_product(self):
        self.ensure_one()
        if self.product_id:
            return self.product_id

        existing = self._find_matching_odoo_product()
        if existing:
            product = existing
            matched = True
            note = _('Linked to existing product "%s" (matched by internal reference/barcode '
                      '"%s") instead of creating a duplicate.') % (product.display_name, self.sku)
        else:
            product = self.env['product.product'].create({
                'name': self.name,
                'default_code': self.sku,
                'list_price': self.price,
                'sale_ok': True,
                'is_foodics_product': True,
                'foodics_mapping_id': self.id,
            })
            matched = False
            note = _('Created from a Foodics menu sync.')

        if matched and not product.is_foodics_product:
            product.write({'is_foodics_product': True, 'foodics_mapping_id': self.id})

        self.write({
            'product_id': product.id,
            'matched_existing_product': matched,
            'note': note,
            'state': 'approved',
        })
        return product

    def action_approve(self):
        for rec in self:
            rec._create_or_link_product()

    def action_reject(self):
        self.write({'state': 'rejected'})

    def action_reset_to_approve(self):
        """Send a rejected row back to the queue - does not touch/undo a
        product that was already created/linked while this was approved.
        """
        self.write({'state': 'to_approve'})
