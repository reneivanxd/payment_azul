# -*- coding: utf-8 -*-

{
    'name': 'Azul Payment Acquirer',
    'category': 'Accounting',
    'summary': 'Payment Acquirer: Azul Implementation',
    'version': '1.0',
    'description': """Azul Payment Acquirer""",
    'depends': ['payment'],
    'data': [
        'views/payment_views.xml',
        'views/payment_azul_templates.xml',
        'data/payment_acquirer_data.xml',
    ],
    'installable': True,
    'post_init_hook': 'create_missing_journal_for_acquirers',
}
