# -*- coding: utf-8 -*-

import logging
import pprint
import werkzeug

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AzulController(http.Controller):

    @http.route([
        '/payment/azul/approved',
        '/payment/azul/declined'], type='http', auth='none', csrf=False)
    def azul_return(self, **post):
        _logger.info('azul_return: post data %s', pprint.pformat(post))
        request.env['payment.transaction'].sudo().form_feedback(post, 'azul')
        return werkzeug.utils.redirect(post.get('return_url', '/'))

    @http.route('/payment/azul/cancel', type='http', auth='none', csrf=False)
    def azul_cancel(self, **post):
        _logger.info('azul_cancel: post data %s', pprint.pformat(post))
        # post.update({
        #     'ResponseMessage': 'CANCELADA'
        # })
        # request.env['payment.transaction'].sudo().form_feedback(post, 'azul')
        return werkzeug.utils.redirect(post.get('return_url', '/'))
