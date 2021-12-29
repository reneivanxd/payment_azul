# coding: utf-8
import hashlib
import hmac
import logging
import pprint

from werkzeug import urls

from odoo import api, fields, models, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.tools.float_utils import float_compare, float_repr, float_round
from odoo.http import request

_logger = logging.getLogger(__name__)


# def normalize_keys_upper(data):
#     """Set all keys of a dictionnary to uppercase

#     Buckaroo parameters names are case insensitive
#     convert everything to upper case to be able to easily detected the presence
#     of a parameter by checking the uppercase key only
#     """
#     return {key.upper(): val for key, val in data.items()}


class AzulPaymentAcquirer(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('azul', 'Azul')])
    azul_merchant_id = fields.Char(
        'MerchantId', required_if_provider='azul', groups='base.group_user')
    azul_merchant_type = fields.Char(
        'MerchantType', required_if_provider='azul', groups='base.group_user')
    azul_auth_key = fields.Char(
        'AuthKey', required_if_provider='azul', groups='base.group_user')

    _azul_auth_hash_fields = {
        'out': ['OrderNumber', 'Amount', 'AuthorizationCode', 'DateTime', 'ResponseCode', 'IsoCode', 'ResponseMessage', 'ErrorDescription', 'RRN'],
        'in': ['Azul_MerchantId', 'Azul_MerchantName', 'Azul_MerchantType', 'Azul_CurrencyCode', 'Azul_OrderNumber', 'Azul_Amount', 'Azul_ITBIS', 'Azul_ApprovedUrl', 'Azul_DeclinedUrl', 'Azul_CancelUrl', 'Azul_UseCustomField1', 'Azul_CustomField1Label', 'Azul_CustomField1Value', 'Azul_UseCustomField2', 'Azul_CustomField2Label', 'Azul_CustomField2Value']
    }

    _approved_url = '/payment/azul/approved'
    _cancel_url = '/payment/azul/cancel'
    _declined_url = '/payment/azul/declined'

    def _get_azul_urls(self, environment):
        """ Azul URLs
        """
        if environment == 'prod':
            return 'https://pagos.azul.com.do/paymentpage/Default.aspx'
        else:
            return 'https://pruebas.azul.com.do/paymentpage/Default.aspx'

    def _azul_generate_digital_sign(self, inout, values):
        """ Generate the shasign for incoming or outgoing communications.

        :param browse acquirer: the payment.acquirer browse record. It should
                                have a shakey in shaky out
        :param string inout: 'in' (odoo contacting azul) or 'out' (azul
                             contacting odoo).
        :param dict values: transaction values

        :return string: shasign
        """
        assert inout in ('in', 'out')
        assert self.provider == 'azul'

        keys = self._azul_auth_hash_fields[inout]
        values_dict = dict(values or {})

        _logger.info('_azul_generate_digital_sign: values=%s, inout=%s, keys=%s',
                     pprint.pformat(values_dict), inout, keys)

        def get_value(key):
            return str(values_dict.get(key, ''))

        sign = ''.join([get_value(key) for key in keys])
        # Add the pre-shared secret key at the end of the signature
        sign = sign + str(self.azul_auth_key)
        _logger.info('_azul_generate_digital_sign: sign=%s', sign)

        return hmac.new(str(self.azul_auth_key).encode('utf-8'), sign.encode('utf-16le'), hashlib.sha512).hexdigest()

    @api.multi
    def azul_form_generate_values(self, values):
        base_url = self.get_base_url()
        azul_tx_values = dict(values)
        azul_tx_values.update({
            'Azul_MerchantId': self.azul_merchant_id,
            'Azul_MerchantName': self.company_id.name,
            'Azul_MerchantType': self.azul_merchant_type,
            'Azul_CurrencyCode': '$',
            # 'Azul_CurrencyCode': values['currency'] and values['currency'].name or '$',
            'Azul_OrderNumber': values['reference'],
            'Azul_Amount': float_repr(float_round(values['amount'], 2) * 100, 0),
            'Azul_ITBIS': float_repr(float_round(self.sale_order_id.amount_tax, 2) * 100, 0),
            'Azul_ApprovedUrl': urls.url_join(base_url, self._approved_url) + "?return_url=%s" % (azul_tx_values.get('return_url', '/')),
            'Azul_CancelUrl': urls.url_join(base_url, self._cancel_url) + "?return_url=%s&OrderNumber=%s" % (azul_tx_values.get('return_url', '/'), values['reference']),
            'Azul_DeclinedUrl': urls.url_join(base_url, self._declined_url) + "?return_url=%s" % (azul_tx_values.get('return_url', '/')),

            'Azul_UseCustomField1': '0',
            'Azul_CustomField1Label': '',
            'Azul_CustomField1Value': '',

            'Azul_UseCustomField2': '0',
            'Azul_CustomField2Label': '',
            'Azul_CustomField2Value': '',
            # 'Brq_culture': (values.get('partner_lang') or 'en_US').replace('_', '-'),
        })
        azul_tx_values['Azul_AuthHash'] = self._azul_generate_digital_sign(
            'in', azul_tx_values)

        _logger.info('azul_form_generate_values: values=%s',
                     pprint.pformat(azul_tx_values))
        return azul_tx_values

    @api.multi
    def azul_get_form_action_url(self):
        """ Azul URLs
        """
        if self.environment == 'prod':
            return 'https://pagos.azul.com.do/paymentpage/Default.aspx'
        else:
            return 'https://pruebas.azul.com.do/paymentpage/Default.aspx'

    def get_base_url(self):
        return request and request.httprequest.url_root or self.env['ir.config_parameter'].sudo().get_param('web.base.url')


class AzulPaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    @api.model
    def _azul_form_get_tx_from_data(self, data):
        """ Given a data dict coming from azul, verify it and find the related
        transaction record. """
        _logger.info('_azul_form_get_tx_from_data: data=%s',
                     pprint.pformat(data))
        origin_data = dict(data)

        reference, status_code, shasign = origin_data.get(
            'OrderNumber'), data.get('ResponseMessage', '').upper(), origin_data.get('AuthHash', '')
        if not reference:
            error_msg = _('Azul: received data with missing reference (%s)') % (
                reference)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        tx = self.search([('reference', '=', reference)])
        if not tx or len(tx) > 1:
            error_msg = _(
                'Azul: received data for reference %s') % (reference)
            if not tx:
                error_msg += _('; no order found')
            else:
                error_msg += _('; multiple order found')
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        if status_code == 'CANCELADA':
            return tx

        # verify shasign
        shasign_check = tx.acquirer_id._azul_generate_digital_sign(
            'out', origin_data)
        if shasign_check != shasign:
            error_msg = _('Azul: invalid shasign, received %s, computed %s, for data %s') % (
                shasign, shasign_check, data)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        return tx

    def _azul_form_get_invalid_parameters(self, data):
        _logger.info('_azul_form_get_invalid_parameters: data=%s',
                     pprint.pformat(data))
        invalid_parameters = []
        if self.acquirer_reference and data.get('AzulOrderId', '') != self.acquirer_reference:
            invalid_parameters.append(
                ('Transaction Id', data.get('AzulOrderId', ''), self.acquirer_reference))

        if data.get('ResponseMessage', '').upper() == 'CANCELADA':
            return invalid_parameters

        # check what is buyed
        amount = float_repr(float_round(self.amount, 2) * 100, 0)
        if data.get('Amount') != amount:
            invalid_parameters.append(
                ('Amount', data.get('Amount'), float_repr(float_round(self.amount, 2) * 100, 0)))

        return invalid_parameters

    def _azul_form_validate(self, data):
        _logger.info('_azul_form_validate: data=%s', pprint.pformat(data))
        data = dict(data)
        status_code = data.get('ResponseMessage', '').upper()
        if status_code == 'APROBADA':
            self.write({
                'state': 'done',
                'acquirer_reference': data.get('AzulOrderId', ''),
            })
            return True
        elif status_code == 'DECLINADA':
            self.write({
                'state': 'error',
                'state_message': data.get('ErrorDescription', 'Azul: feedback error'),
                'acquirer_reference': data.get('AzulOrderId', ''),
            })
            return True
        elif status_code == 'CANCELADA':
            self.write({
                'state': 'cancel',
                'acquirer_reference': '',
            })
            return True
        else:
            _logger.error("_azul_form_validate: data=%s", pprint.pformat(data))
            self.write({
                'state': 'error',
                'state_message': data.get('ErrorDescription', 'Azul: feedback error'),
                'acquirer_reference': data.get('AzulOrderId', ''),
            })
            return False
