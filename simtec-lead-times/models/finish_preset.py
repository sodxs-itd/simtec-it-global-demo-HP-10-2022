import datetime
import logging

from odoo import models, fields, api, _
from odoo.osv import expression
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_round, float_is_zero
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)


class FinishPresetLine(models.Model):
    _name = 'finish_preset_line'

    attribute_id = fields.Many2one('product.attribute', string="Attribute")
    attribute_value_id = fields.Many2one('product.attribute.value', string="Attribute Value")
    finish_preset_id = fields.Many2one('finish_preset')
    product_attribute_ids = fields.Many2many('product.attribute', string="Product Attributes", compute="_compute_product_attributes", store=True)
    product_attribute_value_ids = fields.Many2many('product.attribute.value', string="Product Attribute Values", compute="_compute_product_attributes_values", store=True)
    attribute_values_string= fields.Char(string="Attribute Values String", compute="_compute_attribute_values_string", store=True)


    @api.depends("finish_preset_id.product_tmpl_id")
    def _compute_product_attributes(self):
        for record in self:
            ids = []
            for attribute in record.finish_preset_id.product_tmpl_id.attribute_line_ids:
                ids.append(attribute.attribute_id.id)
            record['product_attribute_ids'] = [(6, 0, ids)]


    @api.depends("attribute_id")
    @api.depends("finish_preset_id.product_tmpl_id")
    def _compute_product_attributes_values(self):
        for record in self:
            ids = []
            for attribute in record.finish_preset_id.product_tmpl_id.attribute_line_ids:
                if attribute.attribute_id.id == record.attribute_id.id:
                    for value in attribute.value_ids:
                        ids.append(value.id)
            record['product_attribute_value_ids'] = [(6, 0, ids)]


    @api.depends("finish_preset_id.product_tmpl_id")
    def _compute_attribute_values_string(self):
        for record in self:
            ids = []
            for attribute in record.finish_preset_id.product_tmpl_id.attribute_line_ids:
                if attribute.attribute_id.id == record.attribute_id.id:
                    for value in attribute.value_ids:
                        ids.append(value)
            s = ""
            for id in ids:
                s += str(id.name) + ", "
            record['attribute_values_string'] = s
        


class FinishExclusionLine(models.Model):
    _name = 'finish_exclusion_line'

    attribute_id = fields.Many2one('product.attribute', string="Attribute")
    attribute_value_id = fields.Many2one('product.attribute.value', string="Attribute Value")
    finish_preset_id = fields.Many2one('finish_preset')
    product_attribute_ids = fields.Many2many('product.attribute', string="Product Attributes", compute="_compute_product_attributes", store=True)
    product_attribute_value_ids = fields.Many2many('product.attribute.value', string="Product Attribute Values", compute="_compute_product_attributes_values", store=True)
    attribute_values_string= fields.Char(string="Attribute Values String", compute="_compute_attribute_values_string", store=True)


    @api.depends("finish_preset_id.product_tmpl_id")
    def _compute_product_attributes(self):
        for record in self:
            ids = []
            for attribute in record.finish_preset_id.product_tmpl_id.attribute_line_ids:
                ids.append(attribute.attribute_id.id)
            record['product_attribute_ids'] = [(6, 0, ids)]


    @api.depends("attribute_id")
    @api.depends("finish_preset_id.product_tmpl_id")
    def _compute_product_attributes_values(self):
        for record in self:
            ids = []
            for attribute in record.finish_preset_id.product_tmpl_id.attribute_line_ids:
                if attribute.attribute_id.id == record.attribute_id.id:
                    for value in attribute.value_ids:
                        ids.append(value.id)
            record['product_attribute_value_ids'] = [(6, 0, ids)]


    @api.depends("finish_preset_id.product_tmpl_id")
    def _compute_attribute_values_string(self):
        for record in self:
            ids = []
            for attribute in record.finish_preset_id.product_tmpl_id.attribute_line_ids:
                if attribute.attribute_id.id == record.attribute_id.id:
                    for value in attribute.value_ids:
                        ids.append(value)
            s = ""
            for id in ids:
                s += str(id.name) + ", "
            record['attribute_values_string'] = s

class FinishPreset(models.Model):
    _name = 'finish_preset'

    def _get_default_product(self):
        for record in self:
            if self.env.context.get('active_id') and int(self.env.context.get('active_id')):
                self['product_tmpl_id'] = int(self.env.context.get('active_id'))

    name = fields.Char(string="Name")
    product_tmpl_id = fields.Many2one('product.template', string="Product Template", default=_get_default_product)
    max_lead_time = fields.Integer(string="Maximum Lead Time", help="Number of days for which to consider an incoming shipment with this preset instead of an earlier one.\nExample: we need to match a product, we have 2 incoming containers, \nwe only look at if the second one would be preferable to the first if it arrives \nwithin X days after the first (X being the Maximum Lead Time here)")
    priority = fields.Integer(string="Priority", help="Lower number means a higher priority. How the software will prioritize this finish preset compared to the other ones that might be available.")
    finish_line_ids = fields.One2many('finish_preset_line', 'finish_preset_id', string="Finish Lines", help="Lines that define the attributes of the finish preset.")
    exclusion_line_ids = fields.One2many('finish_exclusion_line', 'finish_preset_id', string="Exclusion Lines", help="Lines that define the exclusions of the finish preset.\nAn attribute added to the exclusions is an attribute which, if selected on an order, \nNO incoming shipment with the current finish preset can be used to fulfill that order.\nExample: Table with a line on finish preset [shape: round] cannot be used for \nan order where the same table was ordered with [shape: square] if [shape: square] is added as an exclusion line.")
    po_line_ids = fields.One2many('purchase.order.line', 'finish_preset_id', string="Purchase Order Lines", help="Purchase Order Lines that use this specific finish preset.")

    

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    finish_preset_id = fields.Many2one('finish_preset', string="Finish Preset")
    product_tmpl_id = fields.Many2one('product.template', string="Product Template", related='product_id.product_tmpl_id')

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    finish_preset_ids = fields.One2many('finish_preset', 'product_tmpl_id', string="Finish Presets")

    