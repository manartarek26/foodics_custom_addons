"""Fake Foodics purchasing endpoints.

Purely additive extension of the foodics_mock server (the original module
is not modified). All routes live under the same /foodics_mock/v5 namespace
and reuse the mock's Bearer-token authentication.

Request/response shapes follow the local API Docs PDFs:
  - Suppliers                    (resources/suppliers.html)
  - Purchase Order               (resources/purchase_orders.html)
  - Inventory Items              (resources/inventory_items.html)
  - Inventory Transactions       (resources/inventory_transactions.html)
  - Cost Adjustment Transactions (resources/cost_adjustment_transactions.html)
  - Inventory Levels             (resources/inventory_levels.html)
  - Pagination                   (core: page param, 50/page, links+meta)
  - Errors                       (401/404/422 with errors{...} map)
"""
import json
import uuid

from odoo import fields, http
from odoo.http import request, Response

from odoo.addons.foodics_mock.controllers.main import _find_app_by_token

PER_PAGE = 50  # docs "Pagination": "the API returns 50 objects per page"

# Docs "Purchase Order > Statuses"
PO_STATUS_DRAFT = 1
PO_STATUS_PENDING = 2
PO_STATUS_APPROVED = 3
PO_STATUS_DECLINED = 4
PO_STATUS_PARTIAL = 5
PO_STATUS_CLOSED = 6
# Docs "Inventory Transactions > Types / Statuses"
TX_TYPE_PURCHASING = 1
TX_TYPE_RETURN_TO_SUPPLIER = 4
TX_STATUS_CLOSED = 4


def _json(data, status=200):
    return Response(json.dumps(data), status=status,
                    content_type='application/json; charset=utf-8')


def _body(req):
    try:
        return json.loads(req.httprequest.data or b'{}')
    except ValueError:
        return {}


def _invalid(errors):
    """Docs Errors section: 422 payload problems return an errors map."""
    return _json({'message': 'The given data was invalid.', 'errors': errors}, status=422)


def _paginate(records):
    page = int(request.httprequest.args.get('page', 1) or 1)
    total = len(records)
    last_page = max(1, -(-total // PER_PAGE))
    page = min(max(1, page), last_page)
    chunk = records[(page - 1) * PER_PAGE: page * PER_PAGE]
    path = request.httprequest.base_url
    return {
        'data': chunk,
        'links': {
            'first': f'{path}?page=1',
            'last': f'{path}?page={last_page}',
            'prev': f'{path}?page={page - 1}' if page > 1 else None,
            'next': f'{path}?page={page + 1}' if page < last_page else None,
        },
        'meta': {
            'current_page': page,
            'from': (page - 1) * PER_PAGE + 1 if total else None,
            'last_page': last_page,
            'path': path,
            'per_page': PER_PAGE,
            'to': min(page * PER_PAGE, total) if total else None,
            'total': total,
        },
    }


class FoodicsMockPurchaseController(http.Controller):

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _app(self):
        app = _find_app_by_token(request)
        if app:
            app._ensure_purchase_seed()
        return app

    def _updated_after(self):
        return request.httprequest.args.get('updated_after')

    def _filter_updated(self, records):
        after = self._updated_after()
        if after:
            records = records.filtered(
                lambda r: fields.Datetime.to_string(r.write_date or r.create_date) > after)
        return records

    @staticmethod
    def _deleted(record):
        return bool(record.deleted_at)

    def _wants_deleted(self):
        return request.httprequest.args.get('is_deleted') in ('1', 'true', 'True')

    # ==================================================================
    # SUPPLIERS  (docs: GET/POST /suppliers, GET/PUT/DELETE /suppliers/{id},
    #             PUT /suppliers/{id}/restore, POST|DELETE item attach)
    # ==================================================================
    # Single method-aware dispatcher per path - registering an unconstrained
    # GET twin plus a methods=['POST'] sibling on the same URL makes route
    # resolution ambiguous across Odoo/Werkzeug versions.
    @http.route('/foodics_mock/v5/suppliers', type='http', auth='public',
                website=False, csrf=False, methods=['GET', 'POST'])
    def suppliers(self, **kwargs):
        if request.httprequest.method == 'POST':
            return self.supplier_create()
        return self.suppliers_list()

    def suppliers_list(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        Supplier = request.env['foodics.mock.supplier'].sudo()
        recs = Supplier.search([('app_id', '=', app.id)], order='create_date')
        if not self._wants_deleted():
            recs = recs.filtered(lambda r: not self._deleted(r))
        for field in ('id', 'name', 'contact_name', 'email', 'phone', 'code'):
            val = request.httprequest.args.get(field)
            if val:
                fname = 'foodics_id' if field == 'id' else field
                recs = recs.filtered(lambda r, f=fname, v=val: (r[f] or '') == v)
        if request.httprequest.args.get('items.id'):
            item_id = request.httprequest.args.get('items.id')
            recs = recs.filtered(lambda r: any(l.item_id.foodics_id == item_id
                                               for l in r.item_ids))
        recs = self._filter_updated(recs)
        return _json(_paginate([r.api_dump() for r in recs]))

    @http.route('/foodics_mock/v5/suppliers/<string:supplier_id>', type='http',
                auth='public', website=False, csrf=False, methods=['GET'])
    def supplier_get(self, supplier_id, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        sup = self._find(app, 'foodics.mock.supplier', supplier_id)
        if not sup:
            return _json({'message': 'Not found'}, status=404)
        return _json({'data': sup.api_dump()})

    def supplier_create(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        payload = _body(request)
        errors = {}
        if not payload.get('name'):
            errors['name'] = ['The name field is required.']
        if errors:
            return _invalid(errors)
        sup = request.env['foodics.mock.supplier'].sudo().create({
            'app_id': app.id,
            'name': payload['name'],
            'contact_name': payload.get('contact_name'),
            'phone': payload.get('phone'),
            'email': payload.get('email'),
            'code': payload.get('code'),
        })
        return _json({'data': sup.api_dump()}, status=201)

    @http.route('/foodics_mock/v5/suppliers/<string:supplier_id>', type='http',
                auth='public', website=False, csrf=False, methods=['PUT'])
    def supplier_update(self, supplier_id, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        sup = self._find(app, 'foodics.mock.supplier', supplier_id)
        if not sup:
            return _json({'message': 'Not found'}, status=404)
        payload = _body(request)
        sup.write({k: payload[k] for k in
                   ('name', 'contact_name', 'phone', 'email', 'code')
                   if k in payload})
        if 'tags' in payload and isinstance(payload['tags'], list):
            sup.write({'deleted_at': False})  # tags accepted, kept empty like seed
        return _json({'data': sup.api_dump()})

    @http.route('/foodics_mock/v5/suppliers/<string:supplier_id>', type='http',
                auth='public', website=False, csrf=False, methods=['DELETE'])
    def supplier_delete(self, supplier_id, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        sup = self._find(app, 'foodics.mock.supplier', supplier_id)
        if not sup:
            return _json({'message': 'Not found'}, status=404)
        # soft delete so the documented restore endpoint can bring it back
        sup.deleted_at = fields.Datetime.now()
        return _json({'message': 'Supplier deleted'})

    @http.route('/foodics_mock/v5/suppliers/<string:supplier_id>/restore',
                type='http', auth='public', website=False, csrf=False, methods=['PUT'])
    def supplier_restore(self, supplier_id, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        sup = request.env['foodics.mock.supplier'].sudo().search([
            ('app_id', '=', app.id), ('foodics_id', '=', supplier_id)], limit=1)
        if not sup:
            return _json({'message': 'Not found'}, status=404)
        sup.deleted_at = False
        return _json({'message': 'Supplier restored'})

    @http.route('/foodics_mock/v5/suppliers/<string:supplier_id>/items/<string:item_ext>',
                type='http', auth='public', website=False, csrf=False, methods=['POST'])
    def supplier_attach_item(self, supplier_id, item_ext, **kwargs):
        """Attach Inventory Item to Supplier - docs require order_unit &
        order_to_storage_factor."""
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        sup = self._find(app, 'foodics.mock.supplier', supplier_id)
        item = self._find(app, 'foodics.mock.inventory.item', item_ext)
        if not sup or not item:
            return _json({'message': 'Not found'}, status=404)
        payload = _body(request)
        errors = {}
        if not payload.get('order_unit'):
            errors['order_unit'] = ['The order unit field is required.']
        if 'order_to_storage_factor' not in payload:
            errors['order_to_storage_factor'] = ['The order to storage factor field is required.']
        if errors:
            return _invalid(errors)
        pivot = request.env['foodics.mock.supplier.item'].sudo().search([
            ('supplier_id', '=', sup.id), ('item_id', '=', item.id)], limit=1)
        vals = {
            'order_unit': payload['order_unit'],
            'order_to_storage_factor': payload['order_to_storage_factor'],
            'minimum_order_quantity': payload.get('minimum_order_quantity', 0),
            'cost': payload.get('cost', 0),
            'code': payload.get('code'),
        }
        if pivot:
            pivot.write(vals)
        else:
            vals.update({'supplier_id': sup.id, 'item_id': item.id})
            request.env['foodics.mock.supplier.item'].sudo().create(vals)
        return _json({'data': sup.api_dump()})

    @http.route('/foodics_mock/v5/suppliers/<string:supplier_id>/items/<string:item_ext>',
                type='http', auth='public', website=False, csrf=False, methods=['DELETE'])
    def supplier_detach_item(self, supplier_id, item_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        sup = self._find(app, 'foodics.mock.supplier', supplier_id)
        item = self._find(app, 'foodics.mock.inventory.item', item_ext)
        if not sup or not item:
            return _json({'message': 'Not found'}, status=404)
        request.env['foodics.mock.supplier.item'].sudo().search([
            ('supplier_id', '=', sup.id), ('item_id', '=', item.id)]).unlink()
        return _json({'message': 'Item removed from supplier'})

    # ==================================================================
    # INVENTORY ITEMS  (docs: GET/POST /inventory_items, GET/PUT/DELETE .../{id})
    # ==================================================================
    @http.route('/foodics_mock/v5/inventory_items', type='http', auth='public',
                website=False, csrf=False, methods=['GET', 'POST'])
    def inventory_items(self, **kwargs):
        if request.httprequest.method == 'POST':
            return self.item_create()
        return self.items_list()

    def items_list(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        Item = request.env['foodics.mock.inventory.item'].sudo()
        recs = Item.search([('app_id', '=', app.id)], order='create_date')
        if not self._wants_deleted():
            recs = recs.filtered(lambda r: not self._deleted(r))
        for field in ('id', 'name', 'sku'):
            val = request.httprequest.args.get(field)
            if val:
                fname = 'foodics_id' if field == 'id' else field
                recs = recs.filtered(lambda r, f=fname, v=val: (r[f] or '') == v)
        if request.httprequest.args.get('sku_partial'):
            part = request.httprequest.args.get('sku_partial').lower()
            recs = recs.filtered(lambda r: part in (r.sku or '').lower())
        recs = self._filter_updated(recs)
        return _json(_paginate([r.api_dump() for r in recs]))

    @http.route('/foodics_mock/v5/inventory_items/<string:item_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['GET'])
    def item_get(self, item_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        item = self._find(app, 'foodics.mock.inventory.item', item_ext)
        if not item:
            return _json({'message': 'Not found'}, status=404)
        return _json({'data': item.api_dump()})

    def item_create(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        payload = _body(request)
        errors = {}
        for req_field in ('name', 'sku', 'storage_unit', 'ingredient_unit',
                          'storage_to_ingredient_factor', 'costing_method'):
            if payload.get(req_field) in (None, ''):
                errors[req_field] = [f'The {req_field.replace("_", " ")} field is required.']
        if errors:
            return _invalid(errors)
        item = request.env['foodics.mock.inventory.item'].sudo().create({
            'app_id': app.id,
            'name': payload['name'],
            'sku': payload['sku'],
            'barcode': payload.get('barcode'),
            'storage_unit': payload['storage_unit'],
            'ingredient_unit': str(payload['ingredient_unit']),
            'storage_to_ingredient_factor': float(payload['storage_to_ingredient_factor'] or 1),
            'costing_method': int(payload['costing_method'] or 1),
            'cost': float(payload.get('cost') or 0),
            'minimum_level': float(payload.get('minimum_level') or 0),
            'maximum_level': float(payload.get('maximum_level') or 0),
            'par_level': float(payload.get('par_level') or 0),
            'is_product': bool(payload.get('is_product')),
        })
        return _json({'data': item.api_dump()}, status=201)

    @http.route('/foodics_mock/v5/inventory_items/<string:item_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['PUT'])
    def item_update(self, item_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        item = self._find(app, 'foodics.mock.inventory.item', item_ext)
        if not item:
            return _json({'message': 'Not found'}, status=404)
        payload = _body(request)
        mapping = {
            'name': 'name', 'sku': 'sku', 'barcode': 'barcode',
            'storage_unit': 'storage_unit', 'ingredient_unit': 'ingredient_unit',
            'costing_method': 'costing_method', 'is_product': 'is_product',
        }
        vals = {mapping[k]: payload[k] for k in mapping if k in payload}
        for k in ('storage_to_ingredient_factor', 'cost', 'minimum_level',
                  'maximum_level', 'par_level'):
            if k in payload and payload[k] not in (None, ''):
                vals[k] = float(payload[k])
        item.write(vals)
        return _json({'data': item.api_dump()})

    @http.route('/foodics_mock/v5/inventory_items/<string:item_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['DELETE'])
    def item_delete(self, item_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        item = self._find(app, 'foodics.mock.inventory.item', item_ext)
        if not item:
            return _json({'message': 'Not found'}, status=404)
        item.deleted_at = fields.Datetime.now()
        return _json({'message': 'Item deleted'})

    # ==================================================================
    # PURCHASE ORDERS  (docs: GET/POST /purchase_orders,
    #                   GET/PUT/DELETE /purchase_orders/{id})
    # ==================================================================
    @http.route('/foodics_mock/v5/purchase_orders', type='http', auth='public',
                website=False, csrf=False, methods=['GET', 'POST'])
    def purchase_orders(self, **kwargs):
        if request.httprequest.method == 'POST':
            return self.po_create()
        return self.po_list()

    def po_list(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        PO = request.env['foodics.mock.purchase.order'].sudo()
        recs = PO.search([('app_id', '=', app.id)], order='create_date')
        args = request.httprequest.args
        if args.get('id'):
            recs = recs.filtered(lambda r: r.foodics_id == args['id'])
        if args.get('reference'):
            recs = recs.filtered(lambda r: r.reference == args['reference'])
        if args.get('status'):
            wanted = [int(s) for s in str(args['status']).split(',') if s.strip().isdigit()]
            recs = recs.filtered(lambda r: r.status in wanted)
        if args.get('supplier_id'):
            recs = recs.filtered(lambda r: r.supplier_id == args['supplier_id'])
        if args.get('branch_id'):
            recs = recs.filtered(lambda r: r.branch_id == args['branch_id'])
        if args.get('business_date_after'):
            recs = recs.filtered(lambda r: (r.business_date or '') > args['business_date_after'])
        if args.get('business_date_before'):
            recs = recs.filtered(lambda r: (r.business_date or '') < args['business_date_before'])
        recs = self._filter_updated(recs)
        return _json(_paginate([r.api_dump() for r in recs]))

    @http.route('/foodics_mock/v5/purchase_orders/<string:po_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['GET'])
    def po_get(self, po_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        po = self._find(app, 'foodics.mock.purchase.order', po_ext)
        if not po:
            return _json({'message': 'Not found'}, status=404)
        return _json({'data': po.api_dump()})

    def po_create(self, **kwargs):
        """Docs: create allowed in Draft & Pending only; Pending requires
        submitter_id; id duplicates rejected with a 422 errors map."""
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        PO = request.env['foodics.mock.purchase.order'].sudo()
        payload = _body(request)
        errors = {}
        for req_field in ('branch_id', 'supplier_id', 'creator_id', 'items'):
            if not payload.get(req_field):
                errors[req_field] = [f'The {req_field.replace("_", " ")} field is required.']
        status = int(payload.get('status') or PO_STATUS_DRAFT)
        if status not in (PO_STATUS_DRAFT, PO_STATUS_PENDING):
            errors['status'] = ['Purchase orders can only be created as draft or pending.']
        if status == PO_STATUS_PENDING and not payload.get('submitter_id'):
            errors['submitter_id'] = ['Submitter is required when creating pending orders.']
        if isinstance(payload.get('id'), str) and payload['id'] and PO.search([
                ('app_id', '=', app.id), ('foodics_id', '=', payload['id'])], limit=1):
            errors['id'] = ['Duplicate ID.']
        if errors:
            return _invalid(errors)

        po = PO.create({
            'app_id': app.id,
            # Docs' create requests carry no top-level id; the platform
            # assigns one. Only honour an explicitly provided (idempotency)
            # id, never write None over the required field's default.
            'foodics_id': payload.get('id') or uuid.uuid4().hex[:8],
            'business_date': payload.get('business_date') or fields.Date.to_string(
                fields.Date.today()),
            'delivery_date': payload.get('delivery_date'),
            'reference': payload.get('reference') or f'PO-{po_ref_seq()}',
            'additional_cost': float(payload.get('additional_cost') or 0),
            'status': status,
            'notes': payload.get('notes'),
            'branch_id': payload['branch_id'],
            'supplier_id': payload['supplier_id'],
            'creator_id': payload['creator_id'],
            'submitter_id': payload.get('submitter_id'),
            'poster_id': payload.get('poster_id'),
            'raw_payload': json.dumps(payload, indent=2),
        })
        po.apply_status(status)
        self._upsert_po_lines(po, payload.get('items') or [])
        return _json({'data': po.api_dump()}, status=201)

    @http.route('/foodics_mock/v5/purchase_orders/<string:po_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['PUT'])
    def po_update(self, po_ext, **kwargs):
        """Docs: "You can update the purchase order status to all available
        statuses." Also accepts header/line edits like the doc'd request."""
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        po = self._find(app, 'foodics.mock.purchase.order', po_ext)
        if not po:
            return _json({'message': 'Not found'}, status=404)
        payload = _body(request)
        errors = {}
        if 'status' in payload:
            try:
                new_status = int(payload['status'])
            except (TypeError, ValueError):
                new_status = None
            if new_status not in range(1, 7):
                errors['status'] = ['Invalid purchase order status.']
            elif po.status == PO_STATUS_CLOSED and new_status != PO_STATUS_CLOSED:
                errors['status'] = ['Closed purchase orders cannot be reopened.']
            if new_status == PO_STATUS_PENDING and not (
                    payload.get('submitter_id') or po.submitter_id):
                errors['submitter_id'] = ['Submitter is required for pending status.']
        if errors:
            return _invalid(errors)

        vals = {}
        for k in ('business_date', 'delivery_date', 'reference', 'notes',
                  'branch_id', 'supplier_id', 'creator_id', 'poster_id',
                  'submitter_id'):
            if k in payload:
                vals[k] = payload[k]
        if 'additional_cost' in payload:
            vals['additional_cost'] = float(payload.get('additional_cost') or 0)
        po.write(vals)
        if 'items' in payload:
            self._upsert_po_lines(po, payload['items'])
        if 'status' in payload:
            po.apply_status(int(payload['status']))
        return _json({'data': po.api_dump()})

    @http.route('/foodics_mock/v5/purchase_orders/<string:po_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['DELETE'])
    def po_delete(self, po_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        po = self._find(app, 'foodics.mock.purchase.order', po_ext)
        if not po:
            return _json({'message': 'Not found'}, status=404)
        po.unlink()
        return _json({'message': 'Purchase order deleted'})

    def _upsert_po_lines(self, po, items):
        Line = request.env['foodics.mock.purchase.order.line'].sudo()
        keep_ids = []
        for it in items:
            item = self._find(po.app_id, 'foodics.mock.inventory.item', str(it.get('id')))
            if not item:
                continue
            line = Line.search([('order_id', '=', po.id),
                                ('item_id', '=', item.id)], limit=1)
            qty = float(it.get('quantity') or 0)
            factor = float(it.get('unit_to_storage_factor') or 1)
            received = line.quantity_received if line else 0.0
            vals = {
                'quantity': qty,
                'cost': float(it.get('cost') or 0),
                'unit': it.get('unit') or 'box',
                'unit_to_storage_factor': factor,
                # preserve received progress across edits; clamp to new qty
                'quantity_received': min(received, qty * factor),
            }
            if line:
                line.write(vals)
            else:
                vals.update({'order_id': po.id, 'item_id': item.id})
                line = Line.create(vals)
            keep_ids.append(line.id)
        # drop lines removed by the update payload
        po.line_ids.filtered(lambda l: l.id not in keep_ids).unlink()

    # ==================================================================
    # INVENTORY TRANSACTIONS  (docs: GET/POST /inventory_transactions, ...)
    # Receiving transactions against a purchase order advance that PO's
    # quantity_received and drive its Partially Received / Closed statuses.
    # ==================================================================
    @http.route('/foodics_mock/v5/inventory_transactions', type='http', auth='public',
                website=False, csrf=False, methods=['GET', 'POST'])
    def inventory_transactions(self, **kwargs):
        if request.httprequest.method == 'POST':
            return self.tx_create()
        return self.tx_list()

    def tx_list(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        TX = request.env['foodics.mock.inventory.transaction'].sudo()
        recs = TX.search([('app_id', '=', app.id)], order='create_date')
        args = request.httprequest.args
        if args.get('id'):
            recs = recs.filtered(lambda r: r.foodics_id == args['id'])
        if args.get('type'):
            wanted = [int(t) for t in str(args['type']).split(',') if t.strip().isdigit()]
            recs = recs.filtered(lambda r: r.type in wanted)
        if args.get('status'):
            wanted = [int(s) for s in str(args['status']).split(',') if s.strip().isdigit()]
            recs = recs.filtered(lambda r: r.status in wanted)
        if args.get('reference'):
            recs = recs.filtered(lambda r: r.reference == args['reference'])
        if args.get('supplier_id'):
            recs = recs.filtered(lambda r: r.supplier_id == args['supplier_id'])
        if args.get('branch_id'):
            recs = recs.filtered(lambda r: r.branch_id == args['branch_id'])
        if args.get('purchase_order_id'):
            recs = recs.filtered(lambda r: r.purchase_order_id == args['purchase_order_id'])
        if args.get('invoice_number'):
            recs = recs.filtered(lambda r: (r.invoice_number or '') == args['invoice_number'])
        recs = self._filter_updated(recs)
        return _json(_paginate([r.api_dump() for r in recs]))

    @http.route('/foodics_mock/v5/inventory_transactions/<string:tx_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['GET'])
    def tx_get(self, tx_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        tx = self._find(app, 'foodics.mock.inventory.transaction', tx_ext)
        if not tx:
            return _json({'message': 'Not found'}, status=404)
        return _json({'data': tx.api_dump()})

    def tx_create(self, **kwargs):
        """Docs Create Inventory Transaction request. When the transaction is
        a Purchasing (type 1) linked to a purchase_order_id, the mock also
        advances that PO's quantity_received and flips its status to
        Partially Received (5) / Closed (6) exactly like the real platform."""
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        TX = request.env['foodics.mock.inventory.transaction'].sudo()
        payload = _body(request)
        errors = {}
        if not payload.get('type'):
            errors['type'] = ['The type field is required.']
        else:
            try:
                tx_type = int(payload['type'])
                assert 1 <= tx_type <= 12
            except (TypeError, ValueError, AssertionError):
                errors['type'] = ['Invalid inventory transaction type.']
        if not payload.get('branch_id'):
            errors['branch_id'] = ['The branch id field is required.']
        if not payload.get('creator_id'):
            errors['creator_id'] = ['The creator id field is required.']
        if not payload.get('items'):
            errors['items'] = ['The items field is required.']
        if isinstance(payload.get('id'), str) and payload['id'] and TX.search([
                ('app_id', '=', app.id), ('foodics_id', '=', payload['id'])], limit=1):
            errors['id'] = ['Duplicate ID.']
        if errors:
            return _invalid(errors)

        status = int(payload.get('status') or TX_STATUS_CLOSED)
        tx = TX.create({
            'app_id': app.id,
            # Docs' create requests carry no top-level id; the platform
            # assigns one. Only honour an explicitly provided (idempotency)
            # id, never write None over the required field's default.
            'foodics_id': payload.get('id') or uuid.uuid4().hex[:8],
            'business_date': payload.get('business_date') or fields.Date.to_string(
                fields.Date.today()),
            'reference': payload.get('reference') or f'PUR-{tx_ref_seq()}',
            'type': tx_type,
            'status': status,
            'paid_tax': float(payload.get('paid_tax') or 0),
            'additional_cost': float(payload.get('additional_cost') or 0),
            'notes': payload.get('notes'),
            'invoice_number': payload.get('invoice_number'),
            'invoice_date': payload.get('invoice_date'),
            'branch_id': payload.get('branch_id'),
            'other_branch_id': payload.get('other_branch_id'),
            'supplier_id': payload.get('supplier_id'),
            'reason_id': payload.get('reason_id'),
            'order_id': payload.get('order_id'),
            'creator_id': payload['creator_id'],
            'poster_id': payload.get('poster_id'),
            'tag_id': payload.get('tag_id'),
            'purchase_order_id': payload.get('purchase_order_id'),
            'transfer_order_id': payload.get('transfer_order_id'),
            'other_transaction_id': payload.get('other_transaction_id'),
            'raw_payload': json.dumps(payload, indent=2),
        })
        for it in payload['items']:
            item = self._find(app, 'foodics.mock.inventory.item', str(it.get('id')))
            if not item:
                continue
            request.env['foodics.mock.inventory.transaction.line'].sudo().create({
                'transaction_id': tx.id, 'item_id': item.id,
                'quantity': float(it.get('quantity') or 0),
                'cost': float(it.get('cost') or 0),
            })
        tx.posted_at = fields.Datetime.now()
        if tx_type == TX_TYPE_PURCHASING:
            self._apply_receiving_to_po(app, tx)
        return _json({'data': tx.api_dump()}, status=201)

    def _apply_receiving_to_po(self, app, tx):
        """Mirror the platform behaviour: receiving against a PO advances
        quantity_received (converting storage units back to order units via
        each PO line's unit_to_storage_factor) and sets status 5/6."""
        if not tx.purchase_order_id:
            return
        po = request.env['foodics.mock.purchase.order'].sudo().search([
            ('app_id', '=', app.id), ('foodics_id', '=', tx.purchase_order_id)], limit=1)
        if not po:
            return
        for tl in tx.line_ids:
            pol = po.line_ids.filtered(lambda l: l.item_id == tl.item_id)[:1]
            if not pol:
                continue
            factor = pol.unit_to_storage_factor or 1.0
            received_order_units = tl.quantity / factor if factor else tl.quantity
            pol.quantity_received += received_order_units
        fully = all(l.quantity_received >= l.quantity for l in po.line_ids)
        partially = any(0 < l.quantity_received < l.quantity for l in po.line_ids)
        if fully and po.line_ids:
            po.apply_status(PO_STATUS_CLOSED)
        elif partially:
            po.apply_status(PO_STATUS_PARTIAL)
        elif po.status == PO_STATUS_DRAFT:
            po.apply_status(PO_STATUS_PARTIAL)

    @http.route('/foodics_mock/v5/inventory_transactions/<string:tx_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['PUT'])
    def tx_update(self, tx_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        tx = self._find(app, 'foodics.mock.inventory.transaction', tx_ext)
        if not tx:
            return _json({'message': 'Not found'}, status=404)
        payload = _body(request)
        vals = {}
        for k in ('business_date', 'reference', 'notes', 'invoice_number',
                  'invoice_date', 'branch_id', 'other_branch_id', 'supplier_id',
                  'reason_id', 'creator_id', 'poster_id'):
            if k in payload:
                vals[k] = payload[k]
        for k in ('paid_tax', 'additional_cost'):
            if k in payload:
                vals[k] = float(payload.get(k) or 0)
        if 'status' in payload:
            vals['status'] = int(payload['status'])
        tx.write(vals)
        return _json({'data': tx.api_dump()})

    @http.route('/foodics_mock/v5/inventory_transactions/<string:tx_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['DELETE'])
    def tx_delete(self, tx_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        tx = self._find(app, 'foodics.mock.inventory.transaction', tx_ext)
        if not tx:
            return _json({'message': 'Not found'}, status=404)
        tx.unlink()
        return _json({'message': 'Transaction deleted'})

    # ==================================================================
    # COST ADJUSTMENTS  (docs: List Cost Adjustment Transactions)
    # ==================================================================
    @http.route('/foodics_mock/v5/cost_adjustment_transactions', type='http',
                auth='public', website=False, csrf=False, methods=['GET'])
    def ca_list(self, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        CA = request.env['foodics.mock.cost.adjustment'].sudo()
        recs = CA.search([('app_id', '=', app.id)], order='create_date')
        args = request.httprequest.args
        if args.get('id'):
            recs = recs.filtered(lambda r: r.foodics_id == args['id'])
        if args.get('reference'):
            recs = recs.filtered(lambda r: r.reference == args['reference'])
        if args.get('branch_id'):
            recs = recs.filtered(lambda r: r.branch_id == args['branch_id'])
        recs = self._filter_updated(recs)
        return _json(_paginate([r.api_dump() for r in recs]))

    # ==================================================================
    # INVENTORY LEVELS  (docs: GET /inventory_levels/{branch_id} - rows are
    # {"pivot": {"quantity", "cost_per_unit"}, "id": <inventory item id>};
    # computed live from Purchasing minus Return-to-Supplier transactions.)
    # ==================================================================
    @http.route('/foodics_mock/v5/inventory_levels/<string:branch_ext>', type='http',
                auth='public', website=False, csrf=False, methods=['GET'])
    def levels_list(self, branch_ext, **kwargs):
        app = self._app()
        if not app:
            return _json({'message': 'Unauthenticated'}, status=401)
        TX = request.env['foodics.mock.inventory.transaction'].sudo()
        txs = TX.search([
            ('app_id', '=', app.id),
            ('branch_id', '=', branch_ext),
            ('type', 'in', (TX_TYPE_PURCHASING, TX_TYPE_RETURN_TO_SUPPLIER)),
            ('status', '=', TX_STATUS_CLOSED),
        ])
        levels = {}
        costs = {}
        for tx in txs:
            sign = 1 if tx.type == TX_TYPE_PURCHASING else -1
            for line in tx.line_ids:
                levels[line.item_id.foodics_id] = \
                    levels.get(line.item_id.foodics_id, 0.0) + sign * line.quantity
                costs[line.item_id.foodics_id] = line.cost
        rows = [{'pivot': {'quantity': round(qty, 6),
                           'cost_per_unit': costs.get(iid, 0.0)},
                 'id': iid}
                for iid, qty in levels.items() if abs(qty) > 1e-9]
        only_item = request.httprequest.args.get('inventory_items.id')
        if only_item:
            rows = [r for r in rows if r['id'] == only_item]
        return _json({'data': rows})

    # ------------------------------------------------------------------
    def _find(self, app, model, foodics_id):
        return request.env[model].sudo().search([
            ('app_id', '=', app.id), ('foodics_id', '=', foodics_id)], limit=1)


def po_ref_seq():
    n = request.env['ir.sequence'].sudo().next_by_code('foodics.mock.po.ref')
    return int(n) if n else 1


def tx_ref_seq():
    n = request.env['ir.sequence'].sudo().next_by_code('foodics.mock.tx.ref')
    return int(n) if n else 1
