from odoo import api, fields, models, _

from odoo.addons.foodics_purchase.models.foodics_api import (
    FoodicsApiException,
    RETRYABLE_EXCEPTIONS,
)


class FoodicsConfig(models.Model):
    """Additive purchase-side settings on the existing connection record.

    The foundation module (foodics_base) is NOT modified - this is plain
    Odoo model inheritance. Everything here only ADDS optional behavior.
    """
    _inherit = 'foodics.config'

    # ---- push options -------------------------------------------------
    po_default_creator_id = fields.Char(
        string='PO Creator ID (Foodics User)',
        help='Foodics user id sent as creator_id/poster_id (and submitter_id '
             'when auto-submitting) on created purchase orders. The docs mark '
             'creator as required on POST /purchase_orders but do not define '
             'an app-acting-as-user convention, so it must be configured.')
    po_auto_submit = fields.Boolean(
        string='Auto-submit POs for review',
        help='Push confirmed POs with status 2 (Pending, requires submitter_id) '
             'instead of 1 (Draft). Docs: create allowed in draft & pending only.')
    po_price_tolerance_pct = fields.Float(
        string='Receipt Price Tolerance %',
        help='Receiving at a cost differing from the PO line by more than this '
             'percentage is logged as a warning (non-blocking).')

    # ---- master data options ------------------------------------------
    po_autocreate_items = fields.Boolean(
        string='Auto-create products for new inventory items',
        help='When syncing inventory items, also create a storable '
             'product.product for items that have no linked product yet.')

    # ---- pull cursors & toggles ----------------------------------------
    po_statuses_synced_through = fields.Datetime(
        copy=False, help='updated_after cursor used when polling PO statuses '
                         '(docs List filters).')
    ca_synced_through = fields.Datetime(
        copy=False, help='updated_after cursor used when polling Cost Adjustment '
                         'transactions.')
    po_auto_update_product_cost = fields.Boolean(
        string='Apply cost adjustments to product cost',
        help='Pulled Cost Adjustment transactions update the linked product\'s '
             'standard price automatically.')

    # ---- smart button counts -------------------------------------------
    supplier_count = fields.Integer(compute='_compute_purchase_counts')
    item_count = fields.Integer(compute='_compute_purchase_counts')
    level_count = fields.Integer(compute='_compute_purchase_counts')
    sync_log_count = fields.Integer(compute='_compute_purchase_counts')

    def _compute_purchase_counts(self):
        Log = self.env['foodics.sync.log']
        for rec in self:
            rec.supplier_count = self.env['foodics.supplier'].search_count(
                [('config_id', '=', rec.id)])
            rec.item_count = self.env['foodics.inventory.item'].search_count(
                [('config_id', '=', rec.id)])
            rec.level_count = self.env['foodics.inventory.level'].search_count(
                [('config_id', '=', rec.id)])
            rec.sync_log_count = Log.search_count([('config_id', '=', rec.id)])

    # ------------------------------------------------------------------
    # sync buttons (called from the inherited config form)
    # ------------------------------------------------------------------
    def action_sync_purchase_suppliers(self):
        self.ensure_one()
        count = self.env['foodics.supplier'].action_sync_from_foodics(self)
        return self._notify(_('Suppliers synced'),
                            _('%s supplier(s) fetched from Foodics.') % count)

    def action_sync_inventory_items(self):
        self.ensure_one()
        items, products = self.env['foodics.inventory.item'].action_sync_from_foodics(self)
        message = _('%s item(s) fetched from Foodics.') % items
        if products:
            message += _(' %s new product(s) created.') % products
        return self._notify(_('Inventory Items synced'), message)

    def action_pull_po_statuses(self):
        self.ensure_one()
        updated = self._poll_purchase_order_statuses()
        return self._notify(_('Statuses pulled'),
                            _('%s order(s) refreshed from Foodics.') % updated)

    def action_pull_cost_adjustments(self):
        self.ensure_one()
        applied = self._pull_cost_adjustments()
        return self._notify(_('Cost adjustments pulled'),
                            _('%s item cost(s) updated.') % applied)

    def action_pull_inventory_levels(self):
        self.ensure_one()
        rows = self._pull_inventory_levels()
        return self._notify(_('Levels pulled'), _('%s branch level row(s).') % rows)

    # ------------------------------------------------------------------
    # pollers (also driven by crons; docs have no purchase webhooks)
    # ------------------------------------------------------------------
    def _cron_poll_purchases(self):
        """Cron entry point over every connection."""
        for config in self.search([]):
            try:
                config._poll_purchase_order_statuses()
            except FoodicsApiException as exc:
                _logger.warning('Foodics PO poll failed on %s: %s',
                                config.name, exc.message)
            except RETRYABLE_EXCEPTIONS:
                _logger.warning('Foodics PO poll transient failure on %s',
                                config.name, exc_info=True)
            try:
                config._pull_cost_adjustments()
            except FoodicsApiException as exc:
                _logger.warning('Foodics CA poll failed on %s: %s',
                                config.name, exc.message)
            except RETRYABLE_EXCEPTIONS:
                _logger.warning('Foodics CA poll transient failure on %s',
                                config.name, exc_info=True)

    def _poll_purchase_order_statuses(self):
        """GET /purchase_orders?updated_after=<cursor> (docs List filters).

        Mirrors external status changes into pushed orders: Approved(3),
        Declined(4), Partially Received(5), Closed(6) - including changes
        made from the Foodics console. A 404 means the order was deleted
        upstream and flags the local mirror accordingly.
        """
        self.ensure_one()
        api = self.env['foodics.api']
        params = {}
        if self.po_statuses_synced_through:
            params['updated_after'] = fields.Datetime.to_string(
                self.po_statuses_synced_through)
        rows = api.fetch_purchase_orders(self, params)
        PurchaseOrder = self.env['purchase.order']
        now = fields.Datetime.now()
        touched = 0
        for row in rows:
            po = PurchaseOrder.search([
                ('foodics_po_id', '=', row.get('id'))], limit=1)
            if not po:
                continue
            po._foodics_mirror_status(row)
            touched += 1
        if not self.po_statuses_synced_through or not rows:
            self.po_statuses_synced_through = now
        else:
            self.po_statuses_synced_through = now
        return touched

    def _pull_cost_adjustments(self):
        """GET /cost_adjustment_transactions?updated_after=... (docs section
        "Cost Adjustment Transactions": corrects/overrides item costs).

        Updates foodics.inventory.item.cost and - optionally - the linked
        product's standard_price.
        """
        self.ensure_one()
        api = self.env['foodics.api']
        params = {}
        if self.ca_synced_through:
            params['updated_after'] = fields.Datetime.to_string(self.ca_synced_through)
        rows = api.fetch_cost_adjustments(self, params)
        Item = self.env['foodics.inventory.item']
        applied = 0
        for row in rows:
            for line in row.get('items') or []:
                pivot = line.get('pivot') or {}
                item = Item.search([('config_id', '=', self.id),
                                    ('foodics_id', '=', line.get('id'))], limit=1)
                if not item:
                    continue
                new_cost = float(pivot.get('cost_per_unit') or 0)
                if item.cost != new_cost:
                    item.cost = new_cost
                    applied += 1
                if self.po_auto_update_product_cost and item.product_id:
                    item.product_id.with_context(
                        disable_foodics_sync=True).standard_price = new_cost
        self.ca_synced_through = fields.Datetime.now()
        return applied

    def _pull_inventory_levels(self):
        """Refresh foodics.inventory.level snapshots per mapped branch."""
        self.ensure_one()
        api = self.env['foodics.api']
        branches = self.env['foodics.branch'].search([('config_id', '=', self.id)])
        Level = self.env['foodics.inventory.level']
        rows_done = 0
        for branch in branches:
            data = api.fetch_inventory_levels(self, branch.foodics_id)
            for row in data:
                item = self.env['foodics.inventory.item'].search([
                    ('config_id', '=', self.id),
                    ('foodics_id', '=', row.get('id'))], limit=1)
                if not item:
                    continue
                pivot = row.get('pivot') or {}
                vals = {
                    'quantity': float(pivot.get('quantity') or 0),
                    'cost_per_unit': float(pivot.get('cost_per_unit') or 0),
                    'fetched_at': fields.Datetime.now(),
                }
                level = Level.search([('config_id', '=', self.id),
                                      ('item_id', '=', item.id),
                                      ('branch_id', '=', branch.id)], limit=1)
                if level:
                    level.write(vals)
                else:
                    vals.update({'config_id': self.id, 'item_id': item.id,
                                 'branch_id': branch.id})
                    Level.create(vals)
                rows_done += 1
        return rows_done

    # ------------------------------------------------------------------
    # openers
    # ------------------------------------------------------------------
    def action_open_suppliers(self):
        return self._open_related('foodics.supplier', _('Foodics Suppliers'))

    def action_open_items(self):
        return self._open_related('foodics.inventory.item', _('Foodics Inventory Items'))

    def action_open_levels(self):
        return self._open_related('foodics.inventory.level', _('Stock Levels (Foodics)'))

    def action_open_sync_log(self):
        return self._open_related('foodics.sync.log', _('Purchase Sync Log'))
