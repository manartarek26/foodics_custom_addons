import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from odoo.addons.foodics_purchase.models.foodics_api import (
    FoodicsApiException,
    FoodicsNotFoundError,
)

_logger = logging.getLogger(__name__)


class PurchaseOrder(models.Model):
    """Odoo-driven Foodics purchase orders.

    Implements the API Docs PDF section "Purchase Order":
      - Create  POST /purchase_orders   (draft(1) or pending(2) only;
                pending requires submitter_id)
      - Update  PUT  /purchase_orders/{id}   (any status)
      - Delete  DELETE /purchase_orders/{id}
      - Statuses 1 Draft, 2 Pending, 3 Approved, 4 Declined,
                 5 Partially Received, 6 Closed

    Per the ERP guide, purchase orders upstream are approval requests:
    they never move stock. Actual receiving/returns are pushed by
    stock.picking as Inventory Transactions (types 1 and 4).
    """
    _inherit = 'purchase.order'

    foodics_config_id = fields.Many2one('foodics.config', string='Foodics Connection',
                                        copy=False)
    foodics_branch_id = fields.Many2one(
        'foodics.branch', string='Foodics Branch',
        domain="[('config_id', '=', foodics_config_id)]",
        help='Branch that will receive these goods on the Foodics side.')
    foodics_supplier_id = fields.Many2one(
        'foodics.supplier', compute='_compute_foodics_supplier', store=True)
    foodics_po_id = fields.Char(string='Foodics PO ID', copy=False, index=True,
                                readonly=True)
    foodics_status_raw = fields.Integer(copy=False, readonly=True)
    foodics_state = fields.Selection([
        ('1', 'Draft'), ('2', 'Pending'), ('3', 'Approved'),
        ('4', 'Declined'), ('5', 'Partially Received'), ('6', 'Closed'),
    ], string='Foodics Status', copy=False, readonly=True)
    foodics_sync_state = fields.Selection([
        ('not_synced', 'Not Synced'),
        ('synced', 'Synced'),
        ('error', 'Sync Error'),
    ], default='not_synced', string='Sync State', copy=False, readonly=True)
    foodics_last_sync = fields.Datetime(copy=False, readonly=True)
    foodics_additional_cost = fields.Float(
        string='Additional Cost (Foodics)',
        help="Pass-through of the doc'd additional_cost header field; has no "
             "native Odoo PO equivalent.")
    foodics_note = fields.Text(compute='_compute_foodics_note')

    @api.onchange('foodics_branch_id')
    def _onchange_foodics_branch_id(self):
        """Convenience default: point receiving at the same Odoo warehouse the branch is
        mapped to (foodics.branch.warehouse_id, set under Foodics > Branches), so a PO tagged
        for a branch doesn't silently receive into a different warehouse than the one that
        branch's POS sales deplete (see foodics_accounting: branch -> pos_config_id -> its own
        warehouse). Only overrides when it actually differs, and only via onchange (a plain
        create()/write() - e.g. an import, or a future API entry point - is left alone); see
        foodics_warehouse_warning for the always-accurate check that catches those cases too.
        """
        for po in self:
            warehouse = po.foodics_branch_id.warehouse_id
            if warehouse and warehouse.in_type_id and \
                    po.picking_type_id.warehouse_id != warehouse:
                po.picking_type_id = warehouse.in_type_id

    foodics_warehouse_warning = fields.Char(compute='_compute_foodics_warehouse_warning')

    @api.depends('foodics_branch_id.warehouse_id', 'picking_type_id.warehouse_id')
    def _compute_foodics_warehouse_warning(self):
        """Non-blocking check (same spirit as foodics_currency_warning): flags when this PO
        will receive into a different Odoo warehouse than the one the selected branch is mapped
        to. Never blocks confirm - some setups may deliberately cross-receive - but silently
        letting this drift is exactly what would desync Odoo stock from what the branch's POS
        sales are depleting.
        """
        for po in self:
            warning = False
            branch_wh = po.foodics_branch_id.warehouse_id
            po_wh = po.picking_type_id.warehouse_id
            if branch_wh and po_wh and branch_wh != po_wh:
                warning = _(
                    'This PO receives into warehouse "%(po_wh)s", but branch "%(branch)s" is '
                    'mapped to warehouse "%(branch_wh)s" (Foodics > Branches). Stock bought for '
                    'this branch will land in the wrong warehouse unless that\'s intentional.'
                ) % {'po_wh': po_wh.name, 'branch': po.foodics_branch_id.name,
                     'branch_wh': branch_wh.name}
            po.foodics_warehouse_warning = warning

    @api.depends('partner_id', 'foodics_config_id')
    def _compute_foodics_supplier(self):
        """Resolve the Foodics supplier shadow record matching this PO's
        vendor (docs Suppliers section)."""
        for po in self:
            po.foodics_supplier_id = self.env['foodics.supplier'].search([
                ('config_id', '=', po.foodics_config_id.id or False),
                ('partner_id', '=', po.partner_id.id),
            ], limit=1) if po.partner_id and po.foodics_config_id else \
                self.env['foodics.supplier'].browse()

    @api.depends('foodics_sync_state', 'foodics_state', 'state',
                 'foodics_supplier_id')
    def _compute_foodics_note(self):
        """Human-facing status banner shown on the Foodics tab."""
        for po in self:
            notes = []
            if po.state == 'purchase' and not po.foodics_po_id:
                notes.append(_('Not pushed to Foodics yet.'))
            if po.foodics_currency_warning:
                notes.append(po.foodics_currency_warning)
            if po.foodics_sync_state == 'error':
                last = po._last_failed_log()
                if last:
                    notes.append(last.error_message or _('Last sync failed.'))
            if po.foodics_state == '4':
                notes.append(_('Declined in Foodics - review before continuing.'))
            po.foodics_note = '\n'.join(notes)

    foodics_currency_warning = fields.Char(
        compute='_compute_foodics_currency_warning')

    @api.depends('foodics_config_id', 'company_id')
    def _compute_foodics_currency_warning(self):
        """Docs Settings expose business_currency; multi-currency behavior is
        NOT defined anywhere in the docs, so mismatches are surfaced as a
        non-blocking warning instead of being silently ignored."""
        for po in self:
            warning = False
            config = po.foodics_config_id
            if config and config.business_currency and po.currency_id:
                if config.business_currency.strip().upper() != \
                        (po.currency_id.name or '').strip().upper():
                    warning = _(
                        'Currency mismatch: Foodics business currency is %s '
                        'but this PO is in %s. The docs do not define '
                        'conversion behavior.') % (
                        config.business_currency, po.currency_id.name)
            po.foodics_currency_warning = warning

    # ------------------------------------------------------------------
    # confirmation flow
    # ------------------------------------------------------------------
    def button_confirm(self):
        """Validate mappings BEFORE confirming, then push after super()
        succeeds. Mapping problems block confirmation (user-fixable data);
        transient API failures do NOT roll back the local confirmation -
        Odoo stays the source of truth and the push lands in the retry
        queue instead."""
        for po in self.filtered(lambda p: p.state == 'draft'):
            po._foodics_validate_for_push()
        result = super().button_confirm()
        for po in self:
            if po.state == 'purchase':
                po._foodics_push_create_or_update()
        return result

    def _foodics_validate_for_push(self):
        """Aggregate all blocking checks with actionable messages.

        Implements the documented constraints:
          - creator required on create (attributes table marks creator*)
          - pending needs submitter_id ("Create Purchase order" note)
          - MOQ: "Purchase orders must have quantity greater than or equal
            to minimum order quantity in order unit" (Suppliers pivot docs)
          - items pivot requires unit + factor (PO items sample)
        """
        errors = []
        Api = self.env['foodics.api']
        try:
            config = Api._get_config(self.foodics_config_id or None)
            self.foodics_config_id = config
        except UserError as exc:
            errors.append(str(exc))
            config = None

        if config:
            if not self.foodics_branch_id:
                branch = self.env['foodics.branch'].search(
                    [('config_id', '=', config.id)], limit=1)
                self.foodics_branch_id = branch
            if not self.foodics_branch_id:
                errors.append(_(
                    'No Foodics branch selected/found. Sync branches first '
                    '(Foodics > Connection > Sync Branches), then pick one.'))
            if not self.foodics_supplier_id:
                errors.append(_(
                    'Vendor "%s" is not linked to a Foodics supplier. Sync '
                    'suppliers (Connection form > Sync Suppliers) and make '
                    'sure the vendor matches by code/email.') % self.partner_id.name)
            if not config.po_default_creator_id:
                errors.append(_(
                    'Set "PO Creator ID (Foodics User)" on the connection - the '
                    'docs require creator on created purchase orders.'))

        for line in self.order_line.filtered(
                lambda l: not l.display_type and l.product_qty):
            label = line.product_id.display_name
            if not line.foodics_item_id:
                errors.append(_('Line "%s": no Foodics inventory item mapped. '
                                'Pick one on the line.') % label)
                continue
            if not line.foodics_order_unit or \
                    line.foodics_unit_to_storage_factor <= 0:
                errors.append(_(
                    'Line "%(line)s": item %(item)s has no order unit/factor '
                    'for supplier %(sup)s. Attach the item to this supplier in '
                    'Foodics (POST /suppliers/{id}/items/{item}).') % {
                        'line': label, 'item': line.foodics_item_id.name,
                        'sup': self.foodics_supplier_id.name or '-'})
            if line.foodics_moq and line.product_uom_qty < line.foodics_moq:
                errors.append(_(
                    'Line "%s": quantity %.2f is below the minimum order '
                    'quantity (%.2f %s).') % (label, line.product_uom_qty,
                                              line.foodics_moq,
                                              line.foodics_order_unit or ''))
            if line.foodics_item_id.odoo_uom_id and line.product_uom and \
                    line.foodics_item_id.odoo_uom_id.category_id != \
                    line.product_uom.category_id:
                errors.append(_(
                    'Line "%(line)s": Odoo UoM category (%(a)s) differs from '
                    'the mapped storage unit category (%(b)s). Align either '
                    'side before confirming - quantities are entered in '
                    'supplier order units.') % {
                        'line': label,
                        'a': line.product_uom.category_id.name,
                        'b': line.foodics_item_id.odoo_uom_id.category_id.name})
            if not line.foodics_item_id.odoo_uom_id:
                errors.append(_(
                    'Item "%s" has no Odoo UoM mapping. Map its storage unit '
                    'on the Foodics Inventory Item form first.')
                    % line.foodics_item_id.name)

        if errors:
            raise UserError(
                _('Cannot send this purchase order to Foodics:\n\n- ')
                + '\n\n- '.join(errors))

    def _foodics_build_payload(self, include_items=True):
        """Doc-shaped POST/PUT payload for this PO."""
        self.ensure_one()
        status = 2 if self.foodics_config_id.po_auto_submit else 1
        payload = {
            'status': status,
            # docs: business_date/delivery_date are YYYY-MM-DD strings
            'business_date': fields.Date.to_string(fields.Date.context_today(self)),
            'delivery_date': fields.Date.to_string(self.date_planned),
            'reference': self.name,
            'notes': (self.notes or '')[:500] or None,
            'additional_cost': self.foodics_additional_cost or 0,
            'branch_id': self.foodics_branch_id.foodics_id,
            'supplier_id': self.foodics_supplier_id.foodics_id,
            'creator_id': self.foodics_config_id.po_default_creator_id,
            'poster_id': self.foodics_config_id.po_default_creator_id,
        }
        if status == 2:
            # docs: "If in pending status the submitter_id should be included"
            payload['submitter_id'] = self.foodics_config_id.po_default_creator_id
        if include_items:
            payload['items'] = [
                line._foodics_payload_item()
                for line in self.order_line.filtered(
                    lambda l: not l.display_type and l.product_qty)]
        return payload

    def _foodics_queue_entry(self, operation, method, endpoint, payload=None,
                             params=None):
        Log = self.env['foodics.sync.log']
        return Log.create({
            'config_id': self.foodics_config_id.id,
            'direction': 'push',
            'operation': operation,
            'res_model': self._name,
            'res_id': self.id,
            'method': method,
            'endpoint': endpoint,
            'payload_json': json.dumps(payload) if payload else '',
            'params_json': json.dumps(params) if params else '',
            'state': 'pending',
        })

    def _foodics_push_create_or_update(self):
        """Create upstream (or update when an id already exists - idempotent
        re-push after a previous failure)."""
        self.ensure_one()
        if not self.foodics_supplier_id:
            return  # validation would have blocked confirm; safety net
        updating = bool(self.foodics_po_id)
        operation = 'update_po' if updating else 'create_po'
        endpoint = '/purchase_orders/%s' % self.foodics_po_id if updating \
            else '/purchase_orders'
        entry = self._foodics_queue_entry(operation, 'PUT' if updating else 'POST',
                                          endpoint, self._foodics_build_payload())
        try:
            data = self.env['foodics.api'].execute(entry)
            self._foodics_apply_create_po(entry, data)
        except FoodicsApiException:
            # execute() already recorded details; transient ones are queued
            self.write({'foodics_sync_state': 'error'})

    # ------------------------------------------------------------------
    # queued-success handlers (idempotent; also used by the retry cron)
    # ------------------------------------------------------------------
    def _foodics_apply_create_po(self, entry, data):
        """Store the returned Foodics id/status (docs Create response)."""
        self.ensure_one()
        row = data.get('data') or data
        vals = {
            'foodics_po_id': row.get('id'),
            'foodics_status_raw': int(row.get('status') or 0),
            'foodics_state': str(row.get('status')),
            'foodics_sync_state': 'synced',
            'foodics_last_sync': fields.Datetime.now(),
        }
        self.write(vals)

    def _foodics_apply_update_po(self, entry, data):
        self.ensure_one()
        self._foodics_apply_create_po(entry, data)

    def _foodics_apply_update_po_status(self, entry, data):
        self.ensure_one()
        row = data.get('data') or data
        status = int(row.get('status') or 0)
        self.write({
            'foodics_status_raw': status,
            'foodics_state': str(status),
            'foodics_last_sync': fields.Datetime.now(),
            'foodics_sync_state': 'synced',
        })

    def _foodics_apply_delete_po(self, entry, data):
        """Upstream deletion succeeded (or was already gone): clear link so
        a future reset-to-draft is allowed."""
        self.ensure_one()
        self.write({
            'foodics_po_id': False,
            'foodics_state': False,
            'foodics_status_raw': 0,
            'foodics_sync_state': 'not_synced',
            'foodics_last_sync': fields.Datetime.now(),
        })

    # ------------------------------------------------------------------
    # cancellation / reset
    # ------------------------------------------------------------------
    def button_cancel(self):
        """Cancel locally, then mirror upstream:
          - draft(1) upstream -> DELETE (docs allow delete)
          - other active statuses -> PUT status=4 (Declined)
          - closed(6)/already gone -> nothing possible, logged.
        """
        result = super().button_cancel()
        for po in self.filtered(lambda p: p.foodics_po_id):
            po._foodics_cancel_upstream()
        return result

    def _foodics_cancel_upstream(self):
        self.ensure_one()
        api = self.env['foodics.api']
        config = self.foodics_config_id
        try:
            current = api.get_purchase_order(config, self.foodics_po_id)
            status = int(current.get('status') or 0)
        except FoodicsNotFoundError:
            # deleted upstream meanwhile - just clear our link
            entry = self._foodics_queue_entry(
                'delete_po', 'DELETE', '/purchase_orders/%s' % self.foodics_po_id)
            entry._mark_done('Already gone upstream (404)')
            self._foodics_apply_delete_po(entry, {})
            return
        except FoodicsApiException as exc:
            self.env['foodics.sync.log']._mark_warning_standalone(
                config, _('Could not read Foodics PO %s to cancel: %s')
                % (self.name, exc.message))
            return

        if status == api.PO_STATUS_CLOSED:
            self.env['foodics.sync.log']._mark_warning_standalone(
                config, _('Foodics PO %s is closed upstream; cancellation '
                          'impossible per docs statuses.') % (current.get('reference')
                                                              or self.foodics_po_id))
            return

        if status == api.PO_STATUS_DRAFT:
            entry = self._foodics_queue_entry(
                'delete_po', 'DELETE', '/purchase_orders/%s' % self.foodics_po_id)
        else:
            entry = self._foodics_queue_entry(
                'update_po_status', 'PUT',
                '/purchase_orders/%s' % self.foodics_po_id,
                {'status': api.PO_STATUS_DECLINED})
        try:
            data = api.execute(entry)
            entry._apply_success(data)
        except FoodicsApiException:
            pass  # stays queued/reported via sync log

    def button_draft(self):
        """Block resetting to draft while the order still exists upstream -
        otherwise Odoo/Foodics would diverge silently."""
        for po in self.filtered(lambda p: p.foodics_po_id):
            raise UserError(_(
                'This order was already sent to Foodics (%s). Cancel it first '
                '(which deletes/declines it upstream), then reset to draft.')
                % po.foodics_po_id)
        return super().button_draft()

    # ------------------------------------------------------------------
    # polling support (called by foodics.config pollers)
    # ------------------------------------------------------------------
    def _foodics_refresh_after_movement(self):
        """Best-effort mirror refresh after a receipt/return transaction was
        posted; the upstream platform flips the PO to Partially Received(5)
        / Closed(6)."""
        self.ensure_one()
        if not self.foodics_po_id:
            return
        api = self.env['foodics.api']
        try:
            row = api.get_purchase_order(self.foodics_config_id,
                                         self.foodics_po_id,
                                         log_operation=False)
        except FoodicsApiException:
            return
        except RETRYABLE_EXCEPTIONS:
            return
        if row:
            self._foodics_mirror_status(row)

    def _foodics_mirror_status(self, row):
        """Apply an externally-fetched PO row (docs List response shape) to
        the local mirror, flagging declines made in the Foodics console."""
        self.ensure_one()
        status = int(row.get('status') or 0)
        vals = {
            'foodics_status_raw': status,
            'foodics_state': str(status),
            'foodics_last_sync': fields.Datetime.now(),
        }
        if status == self.env['foodics.api'].PO_STATUS_DECLINED:
            vals['foodics_sync_state'] = 'error'
            self.env['foodics.sync.log'].create({
                'config_id': self.foodics_config_id.id,
                'direction': 'pull',
                'operation': 'mirror_status',
                'res_model': self._name,
                'res_id': self.id,
                'method': 'GET',
                'endpoint': '/purchase_orders/%s' % self.foodics_po_id,
                'state': 'warning',
                'error_message': _('Purchase order %s was DECLINED in Foodics.')
                                 % (row.get('reference') or self.name),
            })
        elif status == self.env['foodics.api'].PO_STATUS_CLOSED:
            vals['foodics_sync_state'] = 'synced'
        else:
            vals['foodics_sync_state'] = 'synced'
        self.write(vals)

    # ------------------------------------------------------------------
    # helpers / actions
    # ------------------------------------------------------------------
    def action_foodics_retry_sync(self):
        """Manual retry: replay this PO's failed pushes."""
        for po in self:
            logs = po._failed_logs()
            if not logs:
                if po.state == 'purchase':
                    po._foodics_push_create_or_update()
                continue
            logs.action_retry()
        return True

    def action_open_foodics_log(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Foodics Sync Log'),
            'res_model': 'foodics.sync.log',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {'default_res_model': self._name, 'default_res_id': self.id},
        }

    def _failed_logs(self):
        self.ensure_one()
        return self.env['foodics.sync.log'].search([
            ('res_model', '=', self._name), ('res_id', '=', self.id),
            ('state', '=', 'error')])

    def _last_failed_log(self):
        self.ensure_one()
        return self._failed_logs()[:1]
