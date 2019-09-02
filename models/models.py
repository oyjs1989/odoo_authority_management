# -*- coding: utf-8 -*-
from odoo import models, fields, api, _, SUPERUSER_ID
from odoo.exceptions import ValidationError

from lxml import etree
import simplejson
import collections
from odoo.osv import orm
from lxml.builder import E
from io import StringIO
import base64


class AccessGroup(models.Model):
    _name = 'authority.management.access'

    rule_id = fields.Many2one('authority.management', required=True)
    model_id = fields.Many2one('ir.model', required=True)
    perm_read = fields.Boolean()
    perm_write = fields.Boolean()
    perm_create = fields.Boolean()
    perm_unlink = fields.Boolean()


class MenuAuth(models.Model):
    _name = 'menu.access.rule'
    menu_id = fields.Many2one('ir.ui.menu', domain=[('child_id', '=', False)])
    auth_id = fields.Many2one("authority.management")
    perm_read = fields.Boolean(default=True)
    perm_write = fields.Boolean()
    perm_create = fields.Boolean()
    perm_unlink = fields.Boolean()


class AuthorityManagement(models.Model):
    _name = "authority.management"

    name = fields.Char()
    groups_id = fields.Many2one('res.groups')
    action_ids = fields.Many2many('ir.actions.act_window')
    views_ids = fields.Many2many('ir.ui.view', 'auth_views_rel')
    access_ids = fields.One2many('authority.management.access', 'rule_id')
    menu_ids = fields.Many2many('ir.ui.menu')
    field_ids = fields.Many2many('ir.model.fields', 'access_view_fields')
    menu_access_ids = fields.One2many('menu.access.rule', 'auth_id')
    access_file = fields.Binary()
    file_name_save = fields.Char(string=_('File Name Save'))
    menu_file = fields.Binary()
    file_menu_save = fields.Char(string=_('File Menu Save'))

    def get_action_from_menu(self, menu):
        return self.env['ir.ui.menu'].browse(menu).mapped('action')

    def get_view_model_from_action(self, action):
        action_id = self.env['ir.actions.act_window'].browse(action)
        model = self.env['ir.model'].search([('model', '=', action_id.res_model)])
        views_ids = action_id.mapped('view_id')
        views_ids |= action_id.mapped('search_view_id')
        for view in action_id.mapped('view_ids').mapped('view_id'):
            views_ids |= view
        return views_ids, model

    def get_all_parent_menu(self, menu):
        menus = self.env['ir.ui.menu']
        if menu.parent_id:
            menus |= menu.parent_id
            up_menu = self.get_all_parent_menu(menu.parent_id)
            menus |= up_menu
        return menus

    def accumulated_permissions(self, all_access):
        '''
        对相同模型的权限去重累加
        :param access:  List
        :return:
        '''
        access_info = {}
        for access in all_access:
            if access.get('model_id') not in access_info:
                access_info[access.get('model_id')] = {
                    'model_id': access.get('model_id'),
                    'perm_read': access.get('perm_read'),
                    'perm_write': access.get('perm_write'),
                    'perm_create': access.get('perm_create'),
                    'perm_unlink': access.get('perm_unlink')}

            else:
                access_info[access.get('model_id')] = {
                    'model_id': access.get('model_id'),
                    'perm_read':  access.get('perm_read') if access.get('perm_read') else access_info[access.get('model_id')].get('perm_read'),
                    'perm_write':  access.get('perm_write') if access.get('perm_write') else access_info[access.get('model_id')].get('perm_write'),
                    'perm_create':  access.get('perm_create') if access.get('perm_create') else access_info[access.get('model_id')].get('perm_create'),
                    'perm_unlink':  access.get('perm_unlink') if access.get('perm_unlink') else access_info[access.get('model_id')].get('perm_unlink')}
        return access_info.values()


    def get_access_from_menu(self, menu_id, perm_write, perm_create, perm_read, perm_unlink):

        def find_field_belong_model(nodes):
            fields = []
            for node in nodes:
                attr = {}
                if node.tag == 'field':
                    attr.update(node.attrib)
                    if node.find('tree'):
                        attr.update(node.find('tree').attrib)
                    fields.append(attr)
                    continue
                if node.getchildren():
                    field = find_field_belong_model(node)
                    fields.extend(field)
                    continue
            return fields

        access_info = {}
        action_id = self.get_action_from_menu(menu_id)
        views, model = self.get_view_model_from_action(action_id.id)
        access_info[model.id] = {
            'model_id': model.id,
            'perm_read': perm_read,
            'perm_write': perm_write,
            'perm_create': perm_create,
            'perm_unlink': perm_unlink}
        for view in views:
            model_name = view.model
            nodes = etree.fromstring(view.arch)
            all_fields = find_field_belong_model(nodes)

            for field in all_fields:
                read, create, write, unlink = False, False, False, False
                if field.get('name') not in self.env[model_name]._fields:
                    continue

                ttype = self.env[model_name]._fields.get(field.get('name')).type
                if ttype == 'many2one':
                    if perm_write or perm_create:
                        read = True

                elif ttype == 'one2many':
                    read = True
                    if field.get('editable'):
                        create = True
                        write = True
                        unlink = True
                        # edit="0" create="0" delete="0" import="0"
                        if field.get('create', '') in ('0', 'false'):
                            create = False
                        if field.get('delete', '') in ('0', 'false'):
                            unlink = False

                elif ttype == 'many2many':
                    if field.get('widget', '') == 'many2many_tags':
                        continue

                    read = True
                    if field.get('editable'):
                        create = True
                        write = True
                        unlink = True
                        # edit="0" create="0" delete="0" import="0"
                        if field.get('create', '') in ('0', 'false'):
                            create = False
                        if field.get('delete', '') in ('0', 'false'):
                            unlink = False

                else:
                    continue

                model = self.env[model_name]._fields.get(field.get('name')).comodel_name
                model_id = self.env['ir.model'].search([('model', '=', model)]).id
                access_info[model_id] = {
                    'model_id': model_id,
                    'perm_read': read,
                    'perm_write': write,
                    'perm_create': create,
                    'perm_unlink': unlink}
        return access_info.values()

    def get_menu_info(self, menu_infos):
        menu_access_ids = []

        for menu_info in menu_infos:
            if menu_info[0]==0:
                menu_access_ids.append(menu_info[2])

            elif menu_info[0]==1:
                nemu_id = self.menu_access_ids.browse(menu_info[1])
                menu = {'menu_id': nemu_id.menu_id.id,
                     'perm_read': nemu_id.perm_read,
                     'perm_write': nemu_id.perm_write,
                     'perm_create': nemu_id.perm_create,
                     'perm_unlink': nemu_id.perm_unlink
                     }
                menu.update(menu_info[2])

        return menu_access_ids

    def get_access(self, menu_access):
        self.access_ids.unlink()
        access = []
        for menu_acc in menu_access:
            access.extend(self.get_access_from_menu(**menu_acc))
        access = self.accumulated_permissions(access)
        ret_access = []
        for acc in access:
            if not acc.get('perm_read'):
                continue
            ret_access.append((0, 0, acc))
        return ret_access

    @api.onchange('menu_access_ids')
    def _onchange_menu_access(self):
        def get_parnets(menu):
            menus = self.env['ir.ui.menu']
            if menu.parent_id:
                menus |= menu.parent_id
                up_menu = get_parnets(menu.parent_id)
                menus |= up_menu
            return menus

        if self.menu_access_ids:
            self.menu_ids = None
            self.views_ids = None
            self.action_ids = self.menu_access_ids.mapped('menu_id').mapped('action')
            for menu in self.menu_access_ids.mapped('menu_id'):
                self.menu_ids |= menu
                self.menu_ids |= get_parnets(menu)
            self.views_ids = self.action_ids.mapped('view_id')
            self.views_ids |= self.action_ids.mapped('search_view_id')
            for view in self.action_ids.mapped('view_ids').mapped('view_id'):
                self.views_ids |= view

    @api.multi
    def write(self, vals):
        if vals.get('menu_access_ids'):
            menu_access_info = self.get_menu_info(vals.get('menu_access_ids'))
            vals['access_ids'] = self.get_access(menu_access_info)
        return super(AuthorityManagement, self).write(vals)

    @api.model
    def create(self, vals):
        if vals.get('menu_access_ids'):
            menu_access_info = self.get_menu_info(vals.get('menu_access_ids'))
            vals['access_ids'] = self.get_access(menu_access_info)
        return super(AuthorityManagement, self).create(vals)

    @api.multi
    def create_access_file(self):
        if self.access_ids:
            group = self.env['ir.model.data'].search([('model', '=', 'res.groups'), ('res_id', '=', self.groups_id.id)])
            group_name = group.name
            module_name = group.module
            file_data = ""
            for access_id in self.access_ids:
                model_name = access_id.model_id.model.replace('.', '_')
                access_str = "access_%s_%s,%s_%s,model_%s,%s.%s,%s,%s,%s,%s\n" % (
                    model_name, group_name, model_name, group_name, model_name, module_name, group_name,
                    '1' if access_id.perm_read else '0', '1' if access_id.perm_write else '0',
                    '1' if access_id.perm_create else '0', '1' if access_id.perm_unlink else '0')
                file_data += access_str
            self.access_file = base64.encodestring(file_data)
            self.file_name_save = 'access_%s.csv' % (group_name)

    @api.multi
    def parse_views(self):
        pass

    def create_menu_groups(self):
        '''
        :param menu_ids:
        :return:
        '''
        if self.menu_ids:
            group = self.env['ir.model.data'].search([('model', '=', 'res.groups'), ('res_id', '=', self.groups_id.id)])
            group_name = group.name
            module_name = group.module
            file_data = ""
            for menu_id in self.menu_ids:
                menu = self.env['ir.model.data'].search([('model', '=', 'ir.ui.menu'), ('res_id', '=', menu_id.id)])
                menu_name = menu.name
                menu_module = menu.module
                file_data += '''<record model="ir.ui.menu" id="%s.%s">\n\t<field name="groups_id"  eval="[(4, ref('%s.%s'))]"/>\n</record>\n''' % (
                menu_module, menu_name, module_name, group_name)
            self.menu_file = base64.encodestring(file_data)
            self.file_menu_save = 'menu_%s.xml' % (group_name)
